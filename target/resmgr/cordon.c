/*
 * /dev/cordon resource manager. QNX 8.0, x86_64.
 *
 * The guarded service. It owns one path in the pathname space, answers a
 * status read, and records write events. Who may read and who may write is
 * decided by the security policy, not by this file: the policy grants the
 * custom permissions "write" and "status" on class "cordon" to some types, and
 * this process asks the policy for the answer on every read and write
 * (secpol_check_permission). With no policy loaded the same code path still
 * runs; libsecpol then evaluates the request the way it documents for a system
 * without policies.
 *
 * Evidence lines go to three places: the serial console, slog2, and the event
 * log file on the writable data partition. Nothing here measures time, rate
 * or resource use; see docs/design.md section 5.
 *
 * Build: qcc -o cordon cordon.c -lsecpol
 */

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include <sys/dispatch.h>
#include <sys/iofunc.h>
#include <sys/resmgr.h>
#include <sys/slog2.h>

#include <secpol/secpol.h>

#define CORDON_PATH      "/dev/cordon"
#define CORDON_LOG       "/data/var/cordon/events.log"
#define CORDON_CLASS     "cordon"
#define CORDON_STATUSBUF 512

static iofunc_attr_t attr;
static slog2_buffer_t slog_buf = NULL;
static int event_fd = -1;
static int event_count;
static int restart_seq;
static const char *zone_name = "zone-main";
static const char *log_path = CORDON_LOG;
static char last_event[256] = "none";
static secpol_permission_t *perm_write;
static secpol_permission_t *perm_status;
static uint32_t object_type = 0;   /* the type our channel carries */
static char status_buf[CORDON_STATUSBUF];

static uint32_t current_policy_id(void)
{
    uint32_t id = 0;
    if (secpol_get_policy_id(&id) != 0) {
        return 0;
    }
    return id;
}

static void emit(const char *kind, const char *detail)
{
    printf("CORDON-OBS service=%s %s\n", kind, detail);
    fflush(stdout);
    if (slog_buf != NULL) {
        slog2f(slog_buf, 0, SLOG2_INFO, "cordon %s %s", kind, detail);
    }
    if (event_fd >= 0) {
        int n = snprintf(NULL, 0, "service %s %s\n", kind, detail);
        char *line = malloc((size_t)n + 1);
        if (line != NULL) {
            snprintf(line, (size_t)n + 1, "service %s %s\n", kind, detail);
            if (write(event_fd, line, (size_t)n) < 0) {
                /* the console line above already carries the fact */
            }
            free(line);
        }
    }
}

static void log_open(void)
{
    const char *slash = strrchr(log_path, '/');
    if (slash != NULL && slash != log_path) {
        char dir[256];
        size_t len = (size_t)(slash - log_path);
        if (len < sizeof dir) {
            memcpy(dir, log_path, len);
            dir[len] = '\0';
            mkdir(dir, 0755);
        }
    }
    event_fd = open(log_path, O_WRONLY | O_CREAT | O_APPEND, 0644);
    if (event_fd < 0) {
        printf("CORDON-OBS service=log-open result=ERR errno=%d\n", errno);
        fflush(stdout);
    }
}

static slog2_buffer_t slog_open(void)
{
    slog2_buffer_set_config_t cfg;
    slog2_buffer_t handle = NULL;

    memset(&cfg, 0, sizeof cfg);
    cfg.num_buffers = 1;
    cfg.buffer_set_name = "cordon";
    cfg.verbosity_level = SLOG2_INFO;
    cfg.buffer_config[0].buffer_name = "cordon";
    cfg.buffer_config[0].num_pages = 2;
    if (slog2_register(&cfg, &handle, 0) != 0) {
        return NULL;
    }
    return handle;
}

/* Name of the type the requesting process is running as, from the message. */
static void client_type_name(const struct _msg_info *info, char *buf, size_t len)
{
    const char *name = secpol_get_type_name(NULL, info->type_id);

    if (name != NULL) {
        snprintf(buf, len, "%s", name);
    } else {
        snprintf(buf, len, "type_id:%u", (unsigned)info->type_id);
    }
}

static int cordon_read(resmgr_context_t *ctp, io_read_t *msg, RESMGR_OCB_T *ocb)
{
    iofunc_ocb_t *o = (iofunc_ocb_t *)ocb;
    int status;
    int total;
    int n;

    if ((status = iofunc_read_verify(ctp, msg, ocb, NULL)) != EOK) {
        return status;
    }
    if ((msg->i.xtype & _IO_XTYPE_MASK) != _IO_XTYPE_NONE) {
        return ENOSYS;
    }
    if (perm_status == NULL ||
        secpol_check_permission(&ctp->info, object_type, perm_status) != 0) {
        char tname[64];
        char detail[160];
        client_type_name(&ctp->info, tname, sizeof tname);
        snprintf(detail, sizeof detail, "kind=status pid=%d type=%s result=DENY check=EPERM",
                 (int)ctp->info.pid, tname);
        emit("deny", detail);
        return EACCES;
    }

    total = snprintf(status_buf, sizeof status_buf,
                     "pid=%d seq=%d zone=%s events=%d policy=0x%08x last=%s\n",
                     (int)getpid(), restart_seq, zone_name, event_count,
                     (unsigned)current_policy_id(), last_event);
    if (total < 0) {
        return EIO;
    }
    if (total >= (int)sizeof status_buf) {
        total = (int)sizeof status_buf - 1;
    }
    if (o->offset >= (off_t)total) {
        _IO_SET_READ_NBYTES(ctp, 0);
        return _RESMGR_NPARTS(0);
    }
    n = total - (int)o->offset;
    if (n > (int)msg->i.nbytes) {
        n = (int)msg->i.nbytes;
    }
    _IO_SET_READ_NBYTES(ctp, n);
    SETIOV(ctp->iov, status_buf + o->offset, n);
    o->offset += n;
    return _RESMGR_NPARTS(1);
}

static int cordon_write(resmgr_context_t *ctp, io_write_t *msg, RESMGR_OCB_T *ocb)
{
    int status;
    int nbytes = msg->i.nbytes;
    char record[256];
    char tname[64];
    char detail[512];

    if ((status = iofunc_write_verify(ctp, msg, ocb, NULL)) != EOK) {
        return status;
    }
    client_type_name(&ctp->info, tname, sizeof tname);

    if (perm_write == NULL ||
        secpol_check_permission(&ctp->info, object_type, perm_write) != 0) {
        snprintf(detail, sizeof detail, "kind=write pid=%d type=%s result=DENY check=EPERM",
                 (int)ctp->info.pid, tname);
        emit("deny", detail);
        return EACCES;
    }

    if (nbytes <= 0) {
        snprintf(detail, sizeof detail, "kind=write pid=%d type=%s result=DENY reason=empty",
                 (int)ctp->info.pid, tname);
        emit("deny", detail);
        return EINVAL;
    }
    if (nbytes > (int)sizeof record - 1) {
        nbytes = (int)sizeof record - 1;
    }
    if (resmgr_msgread(ctp, record, nbytes, sizeof msg->i) != nbytes) {
        return EFAULT;
    }
    record[nbytes] = '\0';
    while (nbytes > 0 && (record[nbytes - 1] == '\n' || record[nbytes - 1] == '\r')) {
        record[--nbytes] = '\0';
    }

    event_count++;
    snprintf(last_event, sizeof last_event, "%s", record);
    snprintf(detail, sizeof detail,
             "kind=write pid=%d type=%s result=ACCEPT bytes=%d record=%s",
             (int)ctp->info.pid, tname, nbytes, record);
    emit("accept", detail);

    _IO_SET_WRITE_NBYTES(ctp, msg->i.nbytes);
    return _RESMGR_NPARTS(0);
}

int main(int argc, char *argv[])
{
    dispatch_t *dpp;
    resmgr_attr_t resmgr_attr;
    resmgr_connect_funcs_t connect_funcs;
    resmgr_io_funcs_t io_funcs;
    dispatch_context_t *ctp;
    const char *path = CORDON_PATH;
    int opt;
    char detail[512];

    while ((opt = getopt(argc, argv, "p:e:n:r:")) != -1) {
        switch (opt) {
        case 'p':
            path = optarg;
            break;
        case 'e':
            log_path = optarg;
            break;
        case 'n':
            zone_name = optarg;
            break;
        case 'r':
            restart_seq = atoi(optarg);
            break;
        default:
            fprintf(stderr, "usage: %s [-p path] [-e eventlog] [-n zone] [-r seq]\n", argv[0]);
            return 2;
        }
    }

    slog_buf = slog_open();
    log_open();

    /* The policy file is read on the target from /proc/boot/secpol.bin. Making
     * it the default handle once is what lets secpol_get_permission() and
     * secpol_get_type_name() resolve names and permissions from it. */
    (void)secpol_open(NULL, SECPOL_USE_AS_DEFAULT);

    /* The object every check is made against is our own channel, which carries
     * the type the policy gave this service. Rules are written against that
     * type name, so the check needs its number, not 0. */
    object_type = secpol_get_type_id(NULL, "cordon_t");
    if (object_type == SECPOL_INVALID_TYPE) {
        object_type = 0;
    }

    perm_write = secpol_get_permission(NULL, CORDON_CLASS, "write", 0);
    perm_status = secpol_get_permission(NULL, CORDON_CLASS, "status", 0);

    dpp = dispatch_create();
    if (dpp == NULL) {
        printf("CORDON-OBS service=start result=ERR reason=dispatch errno=%d\n", errno);
        fflush(stdout);
        return 1;
    }
    memset(&resmgr_attr, 0, sizeof resmgr_attr);
    resmgr_attr.nparts_max = 1;
    resmgr_attr.msg_max_size = 2048;
    iofunc_func_init(_RESMGR_CONNECT_NFUNCS, &connect_funcs, _RESMGR_IO_NFUNCS, &io_funcs);
    iofunc_attr_init(&attr, S_IFCHR | 0666, NULL, NULL);
    io_funcs.read = cordon_read;
    io_funcs.write = cordon_write;

    if (resmgr_attach(dpp, &resmgr_attr, path, _FTYPE_ANY, 0, &connect_funcs, &io_funcs,
                      &attr) == -1) {
        printf("CORDON-OBS service=start result=ERR reason=attach errno=%d path=%s\n",
               errno, path);
        fflush(stdout);
        return 1;
    }

    snprintf(detail, sizeof detail,
             "kind=start seq=%d pid=%d path=%s zone=%s policy=0x%08x object_type=%u "
             "write_perm=%s status_perm=%s",
             restart_seq, (int)getpid(), path, zone_name, (unsigned)current_policy_id(),
             (unsigned)object_type,
             perm_write != NULL ? "present" : "missing",
             perm_status != NULL ? "present" : "missing");
    emit("up", detail);

    ctp = dispatch_context_alloc(dpp);
    if (ctp == NULL) {
        printf("CORDON-OBS service=start result=ERR reason=context errno=%d\n", errno);
        fflush(stdout);
        return 1;
    }
    for (;;) {
        ctp = dispatch_block(ctp);
        if (ctp == NULL) {
            break;
        }
        dispatch_handler(ctp);
    }
    return 0;
}
