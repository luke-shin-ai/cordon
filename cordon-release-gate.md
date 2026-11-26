id: cordon-release-gate
title: Build a Guarded Device Node on QNX 8.0 and Verify It Under QEMU
summary: Write a resource manager at /dev/cordon, protect it with a secpol policy, keep it alive with a supervisor, boot the result in QEMU, and decide from the host whether the image matches the scenario rules.
categories: System Services, Security
tags: intermediate
platforms: QEMU
published: 2026-11-26
featured: false
status: published
authors: ILSOON SHIN
feedback_link: https://github.com/qnx/codelabs/issues

# Build a Guarded Device Node on QNX 8.0 and Verify It Under QEMU

## Overview
Duration: 3:00

In this codelab you build a small QNX 8.0 service that owns one path in the
pathname space, `/dev/cordon`, and you decide from the host whether a boot of
the image matches a written rule set. Four pieces are wired together:

- a security policy that decides which process may read and which may write
- a resource manager that answers a status read and records write events
- a supervisor that starts the service again after it dies
- a boot image, a console capture, and a host gate that applies the rules

Every program, policy and script listing in this codelab is taken from the companion repository
`https://github.com/luke-shin-ai/cordon` at render time, so the listing and the
files in the repository are the same text. Nothing here measures time, rate or
resource use, and the host tool reports its result and leaves the publishing
decision to you.

### What you'll need

- A QNX Software Development Platform 8.0 install. A free non-commercial
  license covers this: [QNX Everywhere](https://qnx.software/en/developers/get-started/qnx-everywhere).
- A Linux host. The QNX 8.0 host toolchain does not run on macOS.
- `qcc`, `mkqnximage` and `secpolcompile` on `PATH`, which the SDP install
  provides after you source `qnxsdp-env.sh`.
- `qemu-system-x86_64` on the host. No target hardware is needed.
- A shell where you can run the image in the foreground. The guest serial
  console is the only output channel used here.

### Check the toolchain

```bash
git clone https://github.com/luke-shin-ai/cordon.git
cd cordon
source ~/qnx800/qnxsdp-env.sh
qcc -V
command -v mkqnximage secpolcompile
qemu-system-x86_64 --version
```

`qcc -V` prints the target list. The target used here is `gcc_ntox86_64`,
which matches the QEMU x86_64 image you build in the next step. `qcc -V` also
prints the version, and `12.2.0` is the one this codelab was run with.

## Build and Boot a QNX 8.0 Image
Duration: 8:00

`mkqnximage` is part of the SDP and builds a bootable QEMU image from your own
file list. It works inside the directory it is started in: the project lives
there, and the build writes its output to `output/` beside the project.

```bash
mkdir -p ~/cordon-lab/base && cd ~/cordon-lab/base
mkqnximage --type=qemu --arch=x86_64 --hostname=cordon --noprompt
```

The build ends with `output/ifs.bin` and `output/disk-qemu`. Boot the stock
image once, before adding anything of your own, so you know the base is good.
Pass the IFS as the kernel and attach the disk:

```bash
qemu-system-x86_64 -cpu max -m 384 -snapshot -nographic \
  -drive file=$PWD/output/disk-qemu,format=raw,if=ide \
  -kernel $PWD/output/ifs.bin
```

On QEMU two details matter. Without `-cpu max` the QNX kernel may stop early
on a CPUID check and print nothing. The image is a multiboot IFS, so pass it
with `-kernel` and also attach the disk.

You are in when the console prints a QNX banner and `Startup complete`, and the
guest shell answers:

```bash
uname -a
```

Leave this shell with `Ctrl-a` then `x`, which stops QEMU and returns you to the
host. The guest has no file share with the host, so a program reaches the
target one way: it is added to the boot image on the host, and it runs from
`/proc/boot` when that image boots. Every program in this lab follows that path.

## Write the Security Policy
Duration: 10:00

`secpol` decides access in the kernel and in the service. A policy file names
types, custom permission classes and the rules between them. The policy comes
first in this codelab because the service code asks it for a decision, and the
policy is what gives those names a meaning.

```text
# Cordon security policy. QNX 8.0 secpol, compiled by secpolcompile into
# /proc/boot/secpol.bin and pushed at run time by secpolpush.
#
# What it controls
#   - who may attach the /dev/cordon path (the guarded service only)
#   - who may connect to the /dev/cordon channel (the clients)
#   - who may use the custom permissions "write" and "status" on class
#     "cordon" (the guarded service asks the policy on every read and write)
#
# Types
#   cordon_t            guarded service and its supervisor
#   cordon_writer_t     permitted writer: write and status
#   cordon_probe_t      unprivileged client: status only, write must be denied
#   cordon_view_t       status consumer (the process the kill-consumer scenario
#                       terminates)
#   cordon_outsider_t   no rule at all: must not even connect
#
# The claim ledger points at the scenarios these rules decide: C2 is
# cordon_probe_t being refused, C3 is cordon_writer_t succeeding.

# The guarded service and its supervisor.
type cordon_t;

# Permitted writer. Claim C3.
type cordon_writer_t;

# Unprivileged client. Claim C2.
type cordon_probe_t;

# Status consumer, terminated by the kill-consumer scenario. Claim C1.
type cordon_view_t;

# A subject the policy never allows to reach /dev/cordon. It needs at least
# one rule of its own to be a real type rather than an unused name; spawn says
# nothing about the guarded channel.
type cordon_outsider_t;

# Custom permissions the resource manager checks.
class cordon { write status };

# Only the guarded service may put a channel at /dev/cordon.
allow_attach cordon_t /dev/cordon;

# The supervisor starts the guarded service as its child.
allow cordon_t self:ability { spawn };

# Clients may reach the channel; the outsider has no such rule.
allow { cordon_writer_t cordon_probe_t cordon_view_t } cordon_t : channel connect;

# Keeps cordon_outsider_t a real type without giving it anything on /dev/cordon.
allow cordon_outsider_t self:ability { spawn };

# Reading status: every client that may connect may read.
allow { cordon_writer_t cordon_probe_t cordon_view_t } cordon_t : cordon status;

# Writing: only the permitted writer.
allow cordon_writer_t cordon_t : cordon write;
```

Three layers are in play, and the file separates them:

1. `allow_attach` decides which process may put a channel at the path.
2. `channel connect` decides which type may reach the service at all. A type
   with no rule, such as `cordon_outsider_t`, is refused by the kernel before
   your code runs.
3. The custom class `cordon` decides the two operations the handlers perform.
   The service asks the policy on every read and write instead of trusting
   itself.

Compile the policy. `mkqnximage --policy` does this for you at image build time;
running `secpolcompile` by hand is how you check the file on its own:

```bash
secpolcompile -o /tmp/secpol-check.bin target/secpol/cordon.policy
```

A refusal from the policy check inside the service comes back to a client as
`errno=13` (`EACCES`). A client type with no connect rule never reaches the
service and gets `errno=1` (`EPERM`) from the kernel.

## Write the Guarded Service
Duration: 15:00

A QNX resource manager registers a path, then receives messages for it. The
kernel routes a client's `open`, `read` and `write` to your handlers. This is
the whole service:

```c
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
```

What to look at, in the order the file presents it:

- `main` calls `dispatch_create`, `iofunc_func_init` and `resmgr_attach` for
  `/dev/cordon`. `resmgr_attach` is the call that makes the path appear.
- `cordon_read` answers a status read with the pid, the restart sequence, the
  zone, the event count, the policy id and the last accepted record.
- `cordon_write` takes the payload with `resmgr_msgread`, records it in slog2
  and in `/data/var/cordon/events.log`, and increments the event count.
- Both handlers ask the policy with `secpol_check_permission` before they do
  their work. `object_type` is the number of the type the channel carries,
  resolved once in `main` with `secpol_get_type_id`. Passing 0 instead of that
  number matches no rule and denies every request.
- The type a client runs as arrives in the message info structure, so the log
  line names the client type the server saw. `secpol_type_id()` in the client
  is only accurate for a process that changes type with
  `secpol_transition_type()`.

Build it with the other three programs in one step:

```bash
make target
```

`make target` runs four `qcc` builds and one `secpolcompile`, with `-Wall
-Wextra`, and prints the files it wrote to `target/build`.

## Talk to the Node From Two Clients
Duration: 10:00

Two small clients make the operations visible from the console. The first one
runs a single operation per invocation and prints one line per fact:

```c
/*
 * cordon-client. One QNX client of /dev/cordon, used by the scenario runner.
 *
 * It does one operation and prints one CORDON-OBS line, so the console log of
 * the target carries the observation rather than a verdict. Whether the
 * observation means PASS or FAIL is decided on the host by cordon run.
 *
 * Which security type this process runs as is not chosen here: the caller sets
 * it with `on -T <type>`. The client prints the type it actually got, taken
 * from libsecpol, which is the type the policy saw.
 *
 * Build: qcc -o cordon-client cordon-client.c -lsecpol
 */

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <secpol/secpol.h>

#define CORDON_PATH "/dev/cordon"

/* libsecpol notes that secpol_type_id() is only accurate for processes that
 * change type with secpol_transition_type(); a process started with `on -T`
 * can report 0 here. The authoritative type of a request is the one the
 * resource manager sees in the message, and that is what it logs. This helper
 * prints what it can and says so when it cannot. */
static void type_name(char *buf, size_t len)
{
    uint32_t id = secpol_type_id();
    const char *name = secpol_get_type_name(NULL, id);

    if (name != NULL) {
        snprintf(buf, len, "%s", name);
    } else if (id != 0 && id != SECPOL_INVALID_TYPE) {
        snprintf(buf, len, "type_id:%u", (unsigned)id);
    } else {
        snprintf(buf, len, "self-unreported");
    }
}

static int obs(const char *role, const char *op, const char *result, const char *extra)
{
    char tname[64];

    type_name(tname, sizeof tname);
    printf("CORDON-OBS client role=%s type=%s op=%s result=%s%s%s\n", role, tname, op, result,
           (extra != NULL && extra[0] != '\0') ? " " : "",
           (extra != NULL && extra[0] != '\0') ? extra : "");
    fflush(stdout);
    return (strcmp(result, "OK") == 0) ? 0 : 1;
}

static int do_status(const char *role, const char *path)
{
    char buf[512];
    ssize_t n;
    int fd = open(path, O_RDONLY);
    char extra[560];

    if (fd < 0) {
        snprintf(extra, sizeof extra, "errno=%d(%s)", errno, strerror(errno));
        return obs(role, "status", "ERR", extra);
    }
    n = read(fd, buf, sizeof buf - 1);
    close(fd);
    if (n <= 0) {
        snprintf(extra, sizeof extra, "errno=%d(%s)", errno, strerror(errno));
        return obs(role, "status", "ERR", extra);
    }
    buf[n] = '\0';
    while (n > 0 && (buf[n - 1] == '\n' || buf[n - 1] == '\r')) {
        buf[--n] = '\0';
    }
    snprintf(extra, sizeof extra, "status=%s", buf);
    return obs(role, "status", "OK", extra);
}

static int do_write(const char *role, const char *path, const char *text)
{
    char extra[320];
    int fd = open(path, O_WRONLY);
    ssize_t n;

    if (fd < 0) {
        snprintf(extra, sizeof extra, "errno=%d(%s)", errno, strerror(errno));
        return obs(role, "write", "ERR", extra);
    }
    n = write(fd, text, strlen(text));
    if (n < 0) {
        snprintf(extra, sizeof extra, "errno=%d(%s)", errno, strerror(errno));
        close(fd);
        return obs(role, "write", "ERR", extra);
    }
    close(fd);
    snprintf(extra, sizeof extra, "bytes=%ld", (long)n);
    return obs(role, "write", "OK", extra);
}

static int do_open(const char *role, const char *path)
{
    char extra[160];
    int fd = open(path, O_WRONLY);

    if (fd < 0) {
        snprintf(extra, sizeof extra, "errno=%d(%s)", errno, strerror(errno));
        return obs(role, "open", "ERR", extra);
    }
    close(fd);
    return obs(role, "open", "OK", "");
}

int main(int argc, char *argv[])
{
    const char *path = CORDON_PATH;
    const char *role = "client";
    const char *op;
    const char *text = "zone=main state=sealed";
    int opt;

    (void)secpol_open(NULL, SECPOL_USE_AS_DEFAULT);

    while ((opt = getopt(argc, argv, "p:r:w:")) != -1) {
        switch (opt) {
        case 'p':
            path = optarg;
            break;
        case 'r':
            role = optarg;
            break;
        case 'w':
            text = optarg;
            break;
        default:
            fprintf(stderr, "usage: %s [-p path] [-r role] [-w text] status|write|open\n",
                    argv[0]);
            return 2;
        }
    }
    if (optind >= argc) {
        fprintf(stderr, "usage: %s [-p path] [-r role] [-w text] status|write|open\n", argv[0]);
        return 2;
    }
    op = argv[optind];

    if (strcmp(op, "status") == 0) {
        return do_status(role, path);
    }
    if (strcmp(op, "write") == 0) {
        return do_write(role, path, text);
    }
    if (strcmp(op, "open") == 0) {
        return do_open(role, path);
    }
    fprintf(stderr, "unknown operation: %s\n", op);
    return 2;
}
```

The second one reads the status in a loop and stays alive until its count runs
out. It is the process a death scenario terminates, and it stands in for a
screen or a dashboard that consumes the service:

```c
/*
 * cordon-view. The status consumer of /dev/cordon.
 *
 * This is the process the kill-consumer scenario terminates. It is NOT the QNX
 * Screen graphics service: this image is built with --graphics=no and has no
 * screen, so a small consumer stands in for the console/status surface that a
 * real system would show. docs/design.md section 1 says so explicitly.
 *
 * It reads the guarded service status repeatedly so that its death is a
 * meaningful event (a consumer that was talking to the service and then went
 * away), and never writes. It writes its own pid to a file so that the
 * scenario runner terminates this process and not a wrapper around it.
 *
 * Build: qcc -o cordon-view cordon-view.c -lsecpol
 */

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <sys/stat.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <secpol/secpol.h>

#define CORDON_PATH "/dev/cordon"

int main(int argc, char *argv[])
{
    const char *path = CORDON_PATH;
    const char *pidfile = "/dev/shmem/cordon-view.pid";
    int ticks = 1000000;
    int i;
    int opt;
    char tname[64];
    const char *name;

    while ((opt = getopt(argc, argv, "p:n:f:")) != -1) {
        switch (opt) {
        case 'p':
            path = optarg;
            break;
        case 'n':
            ticks = atoi(optarg);
            break;
        case 'f':
            pidfile = optarg;
            break;
        default:
            fprintf(stderr, "usage: %s [-p path] [-n ticks] [-f pidfile]\n", argv[0]);
            return 2;
        }
    }

    if (pidfile != NULL) {
        FILE *pf = fopen(pidfile, "w");
        if (pf != NULL) {
            fprintf(pf, "%d\n", (int)getpid());
            fclose(pf);
        }
    }

    (void)secpol_open(NULL, SECPOL_USE_AS_DEFAULT);
    name = secpol_get_type_name(NULL, secpol_type_id());
    snprintf(tname, sizeof tname, "%s", name != NULL ? name : "self-unreported");
    printf("CORDON-OBS view role=view type=%s state=up pid=%d path=%s ticks=%d\n", tname,
           (int)getpid(), path, ticks);
    fflush(stdout);

    for (i = 0; i < ticks; i++) {
        char buf[512];
        int fd = open(path, O_RDONLY);
        if (fd >= 0) {
            ssize_t n = read(fd, buf, sizeof buf - 1);
            close(fd);
            if (n > 0) {
                buf[n] = '\0';
                while (n > 0 && (buf[n - 1] == '\n' || buf[n - 1] == '\r')) {
                    buf[--n] = '\0';
                }
                printf("CORDON-OBS view role=view tick=%d status=%s\n", i, buf);
                fflush(stdout);
            } else {
                printf("CORDON-OBS view role=view tick=%d result=ERR errno=%d\n", i, errno);
                fflush(stdout);
            }
        } else {
            printf("CORDON-OBS view role=view tick=%d result=ERR errno=%d\n", i, errno);
            fflush(stdout);
        }
        sleep(1);
    }
    printf("CORDON-OBS view role=view state=done ticks=%d\n", ticks);
    fflush(stdout);
    return 0;
}
```

Neither client decides anything. Each prints `CORDON-OBS` lines with an
operation, a result and, on failure, an errno. The host reads those lines later.
The client takes its role name from `-r` so that two invocations of the same
binary are distinguishable in one console log.

## Survive a Process Death
Duration: 10:00

A service that dies takes its path with it. On QNX the High Availability
Manager can restart a process, but it is an optional package that a base SDP
install does not include. The primitives a HAM rule drives are in libc, so a
supervisor is short:

```c
/*
 * cordon-supervise. Watches the guarded service and starts it again when it
 * dies.
 *
 * Why this file exists instead of a HAM rule: HAM (the QNX high availability
 * manager) is an optional package in the QNX 8.0 repository
 * (com.qnx.qnx800.target.os_services.ham) but it is not part of the baseline
 * that was installed here, and installing it needs myQNX package-mirror
 * authorization: the install path answers HTTP 401 without credentials, while
 * the baseline install itself did not include ham, libham or the headers.
 * So the restart half of claim C4 is implemented with facilities that are
 * present in the baseline: posix_spawn, waitpid, and slog2, which are the same
 * primitives a HAM rule would drive. docs/design.md section 8 records this,
 * together with what enabling a real HAM entity would take.
 *
 * The supervisor is not a resource manager. It starts the guarded service as
 * its child, so the child inherits the supervisor's security type, and only a
 * process of that type is allowed to attach /dev/cordon.
 *
 * Build: qcc -o cordon-supervise cordon-supervise.c -lsecpol
 */

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

#include <spawn.h>
#include <sys/slog2.h>

#include <secpol/secpol.h>

extern char **environ;

#define CORDON_LOG "/data/var/cordon/events.log"

static slog2_buffer_t slog_buf = NULL;
static int event_fd = -1;
static const char *log_path = CORDON_LOG;

static void emit(const char *what, const char *detail)
{
    printf("CORDON-OBS supervise %s %s\n", what, detail);
    fflush(stdout);
    if (slog_buf != NULL) {
        slog2f(slog_buf, 0, SLOG2_INFO, "cordon-supervise %s %s", what, detail);
    }
    if (event_fd >= 0) {
        int n = snprintf(NULL, 0, "supervise %s %s\n", what, detail);
        char *line = malloc((size_t)n + 1);
        if (line != NULL) {
            snprintf(line, (size_t)n + 1, "supervise %s %s\n", what, detail);
            if (write(event_fd, line, (size_t)n) < 0) {
                /* the console line above already carries the fact */
            }
            free(line);
        }
    }
}

static slog2_buffer_t slog_open(void)
{
    slog2_buffer_set_config_t cfg;
    slog2_buffer_t handle = NULL;

    memset(&cfg, 0, sizeof cfg);
    cfg.num_buffers = 1;
    cfg.buffer_set_name = "cordon-supervise";
    cfg.verbosity_level = SLOG2_INFO;
    cfg.buffer_config[0].buffer_name = "supervise";
    cfg.buffer_config[0].num_pages = 2;
    if (slog2_register(&cfg, &handle, 0) != 0) {
        return NULL;
    }
    return handle;
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
}

int main(int argc, char *argv[])
{
    const char *service = "/proc/boot/cordon";
    const char *path = "/dev/cordon";
    const char *zone = "zone-main";
    int max_restarts = 5;
    int seq;
    int opt;
    char detail[320];

    while ((opt = getopt(argc, argv, "s:p:e:n:m:")) != -1) {
        switch (opt) {
        case 's':
            service = optarg;
            break;
        case 'p':
            path = optarg;
            break;
        case 'e':
            log_path = optarg;
            break;
        case 'n':
            zone = optarg;
            break;
        case 'm':
            max_restarts = atoi(optarg);
            break;
        default:
            fprintf(stderr,
                    "usage: %s [-s service] [-p path] [-e eventlog] [-n zone] [-m max]\n",
                    argv[0]);
            return 2;
        }
    }

    slog_buf = slog_open();
    log_open();
    (void)secpol_open(NULL, SECPOL_USE_AS_DEFAULT);
    {
        const char *self_type = secpol_get_type_name(NULL, secpol_type_id());
        snprintf(detail, sizeof detail, "kind=up pid=%d service=%s max_restarts=%d type=%s",
                 (int)getpid(), service, max_restarts,
                 self_type != NULL ? self_type : "self-unreported");
    }
    emit("start", detail);

    for (seq = 1; seq <= max_restarts; seq++) {
        char seqarg[16];
        char *child_argv[11];
        pid_t pid = -1;
        int status = 0;
        int rc;

        snprintf(seqarg, sizeof seqarg, "%d", seq);
        child_argv[0] = (char *)service;
        child_argv[1] = "-p";
        child_argv[2] = (char *)path;
        child_argv[3] = "-e";
        child_argv[4] = (char *)log_path;
        child_argv[5] = "-n";
        child_argv[6] = (char *)zone;
        child_argv[7] = "-r";
        child_argv[8] = seqarg;
        child_argv[9] = NULL;
        child_argv[10] = NULL;

        rc = posix_spawn(&pid, service, NULL, NULL, child_argv, environ);
        if (rc != 0) {
            snprintf(detail, sizeof detail, "kind=spawn seq=%d result=ERR errno=%d", seq, rc);
            emit("spawn", detail);
            break;
        }
        snprintf(detail, sizeof detail, "kind=spawn seq=%d pid=%d", seq, (int)pid);
        emit("spawn", detail);

        if (waitpid(pid, &status, 0) < 0) {
            snprintf(detail, sizeof detail, "kind=exit seq=%d result=ERR errno=%d", seq, errno);
            emit("exit", detail);
            break;
        }
        snprintf(detail, sizeof detail, "kind=exit seq=%d pid=%d signal=%d code=%d", seq,
                 (int)pid, WIFSIGNALED(status) ? WTERMSIG(status) : 0,
                 WIFEXITED(status) ? WEXITSTATUS(status) : -1);
        emit("exit", detail);

        if (seq < max_restarts) {
            snprintf(detail, sizeof detail, "kind=respawn next_seq=%d", seq + 1);
            emit("respawn", detail);
        }
    }

    snprintf(detail, sizeof detail, "kind=stop spawns=%d", seq - 1 > max_restarts ? max_restarts
                                                                                  : seq - 1);
    emit("stop", detail);
    return 0;
}
```

The supervisor starts the service with `posix_spawn` (the service path comes
from `-s`), waits with `waitpid`, and starts it again when it dies. Each start
carries a new sequence number, passed to the service as `-r`, so a later client
can tell the new process from the dead one. The supervisor writes the spawn,
the exit and the respawn to slog2 and to the event log, because the guarded
service is dead at the moment the restart happens and cannot record it.

## Run the Scenarios at the End of Startup
Duration: 12:00

The scenario runner is a shell script that runs on the target after the system
is up. It pushes the policy, starts the supervisor, and then performs each
scenario in a fixed order, printing one `CORDON-OBS` line per fact:

```bash
#!/bin/sh
# Cordon G2 scenario runner. Runs on the target at the end of system startup
# (from post_startup.sh, see target/boot/build-g2-image.sh) and prints one
# observation line per fact. It asserts nothing: `cordon run` on the host decides
# the result of each scenario from these lines alone.
#
#   cordon-boot.sh normal     service first, then every scenario
#   cordon-boot.sh violated   first client before the service, to show that an
#                             image with the wrong startup order is caught
#
# Every client is started with an explicit security type using `on -T`, so the
# policy decides each request. Nothing here measures time or resource use.

MODE=${1:-normal}
B=/proc/boot
LOG=/data/var/cordon
mkdir -p $LOG

echo "CORDON-G2-START mode=$MODE"

# ---------------------------------------------------------------- policy ----
if secpolpush; then
    echo "CORDON-OBS policy push=OK"
else
    echo "CORDON-OBS policy push=ERR"
fi
echo "CORDON-OBS policy id=$(secpol -Q 2>/dev/null)"
for t in cordon_t cordon_writer_t cordon_probe_t cordon_view_t cordon_outsider_t; do
    echo "CORDON-OBS policy type $t=$(secpol -t $t 2>/dev/null)"
done

# ------------------------------------------------------------- utilities ----
client() {
    # client <type> <role> [client args...]  e.g. ... -w "text" write
    type=$1
    role=$2
    shift 2
    on -T "$type" "$B/cordon-client" -r "$role" "$@"
}

wait_service() {
    i=0
    while [ $i -lt 30 ]; do
        if [ -e /dev/cordon ]; then
            echo "CORDON-OBS service path=/dev/cordon state=present tries=$i"
            return 0
        fi
        sleep 1
        i=$((i + 1))
    done
    echo "CORDON-OBS service path=/dev/cordon state=absent tries=$i"
    return 1
}

start_service() {
    on -T cordon_t "$B/cordon-supervise" -s "$B/cordon" -p /dev/cordon \
        -e "$LOG/events.log" -n zone-main -m 4 &
    wait_service
}

field() {
    # field <name> ; reads one line on stdin and prints the value of key=value.
    # The guarded service status arrives as one field "status=pid=... seq=...",
    # so a leading status= is stripped before the key is compared.
    awk -v k="$1" '{ for (i = 1; i <= NF; i++) {
        f = $i; sub(/^status=/, "", f)
        n = index(f, "="); if (n > 1) {
            if (substr(f, 1, n - 1) == k) { print substr(f, n + 1); exit }
        } } }'
}

kill_pid() {
    # kill_pid <pid> <what> : never call kill with an empty argument
    if [ -n "$1" ]; then
        kill -9 "$1" 2>/dev/null
        echo "CORDON-OBS kill target=$2 pid=$1 result=OK"
    else
        echo "CORDON-OBS kill target=$2 pid=none result=ERR reason=no-pid"
    fi
}

# ------------------------------------------------------- wrong order image ----
if [ "$MODE" = "violated" ]; then
    # The first client runs before the service exists. An image built in this
    # order must be caught: the client cannot reach the guarded path.
    echo "CORDON-OBS order check=first-client-before-service attempting=yes"
    client cordon_view_t order-probe status
    echo "CORDON-OBS order check=first-client-before-service done=yes"
    # Then start the service, so the log also shows the image is otherwise
    # workable and only the order is wrong.
    start_service
    client cordon_view_t order-after-start status
    echo "CORDON-SLOG2-BEGIN"
    slog2info -S
    echo "CORDON-SLOG2-END"
    echo "CORDON-G2-COMPLETE mode=violated"
    exit 0
fi

# ------------------------------------------------------------- normal image --
start_service

# The type each long-lived process actually carries, from the kernel's view.
echo "CORDON-OBS pidin-types-begin"
pidin -F "%a %N %_" 2>/dev/null | grep -F cordon
echo "CORDON-OBS pidin-types-end"

echo "CORDON-OBS scenario name=boot-order step=begin"
client cordon_view_t order-probe status
echo "CORDON-OBS scenario name=boot-order step=end"

# C2: a client without the write permission must be refused, and the service
# must stay up. The outsider has no channel connect rule at all, so it never
# reaches the write handler: two different layers are shown.
echo "CORDON-OBS scenario name=deny-unprivileged-write step=begin"
client cordon_probe_t probe -w "zone=main state=probe" write
client cordon_outsider_t outsider open
client cordon_view_t deny-after status
echo "CORDON-OBS scenario name=deny-unprivileged-write step=end"

# C3: the permitted writer succeeds and one event is recorded.
echo "CORDON-OBS scenario name=allow-privileged-write step=begin"
client cordon_writer_t writer -w "zone=main state=sealed" write
client cordon_view_t writer-after status
echo "CORDON-OBS scenario name=allow-privileged-write step=end"

# C1: the status consumer is killed; the guarded service keeps answering.
echo "CORDON-OBS scenario name=kill-consumer step=begin"
rm -f /dev/shmem/cordon-view.pid
on -T cordon_view_t "$B/cordon-view" -n 60 -f /dev/shmem/cordon-view.pid &
i=0
viewpid=
while [ $i -lt 10 ]; do
    if [ -s /dev/shmem/cordon-view.pid ]; then
        viewpid=$(cat /dev/shmem/cordon-view.pid)
        break
    fi
    sleep 1
    i=$((i + 1))
done
if [ -n "$viewpid" ]; then
    kill_pid "$viewpid" view
else
    echo "CORDON-OBS kill target=view pid=none result=ERR reason=no-pidfile"
fi
sleep 1
if kill -0 "$viewpid" 2>/dev/null; then
    echo "CORDON-OBS view pid=$viewpid state=alive"
else
    echo "CORDON-OBS view pid=$viewpid state=gone"
fi
client cordon_view_t view-after status
echo "CORDON-OBS scenario name=kill-consumer step=end"

# C4: the supervisor starts the guarded service again after it dies.
echo "CORDON-OBS scenario name=supervised-restart step=begin"
st=$(client cordon_view_t pre-kill status)
echo "$st"
svcpid=$(echo "$st" | field pid)
seq0=$(echo "$st" | field seq)
kill_pid "$svcpid" service
i=0
while [ $i -lt 10 ]; do
    sleep 1
    st2=$(client cordon_view_t post-kill status)
    echo "$st2"
    seq1=$(echo "$st2" | field seq)
    if [ -n "$seq1" ] && [ "$seq1" != "0" ] && [ "$seq1" != "$seq0" ]; then
        break
    fi
    i=$((i + 1))
done
client cordon_view_t restart-after status
echo "CORDON-OBS scenario name=supervised-restart step=end"

# Event log as the service wrote it, and the slog2 dump of the whole run.
echo "CORDON-EVENTS-BEGIN"
cat "$LOG/events.log" 2>/dev/null
echo "CORDON-EVENTS-END"
echo "CORDON-SLOG2-BEGIN"
slog2info -S
echo "CORDON-SLOG2-END"
echo "CORDON-G2-COMPLETE mode=normal"
exit 0
```

Read it with the rule set in mind. The script never says whether a scenario
succeeded; it records what each attempt produced, and the host applies the
rule. Every process the scenarios depend on is started with `on -T`, so it
carries the type the policy expects even though the policy is pushed at run
time rather than by the boot script.

## Put Both Images Together
Duration: 12:00

Two images are built from the same sources with the same policy. The only
difference is the startup order: one starts the service before its first
client, the other starts the first client before the service. That difference
is what makes the order check meaningful.

```bash
#!/bin/bash
# Build the Cordon G2 QNX 8.0 QEMU image.
#
#   target/boot/build-g2-image.sh normal     image where the service starts
#                                            before the first client
#   target/boot/build-g2-image.sh violated   image where the first client runs
#                                            before the service exists
#
# Both images are built the same way, from the same sources, with the same
# policy; only the startup order in post_startup.sh differs. That is the point:
# scenario C5 must be able to tell the two apart.
#
# Mechanic: mkqnximage (part of SDP 8.0) generates the IFS and disk images. This
# script lays out a project directory, writes the two mkqnximage snippets that
# add Cordon to it, and runs the build. The QNX kernel in the installed SDP is
# already the symbol-stripped copy described in g0-environment.md (the linker in
# this container is killed on the 70 MB symbol-bearing kernel).
#
# QNX SDP 8.0 must be installed at $SDP (default /workspace/qnx-env/qnx800).
set -uo pipefail

MODE=${1:-normal}
case "$MODE" in
    normal|violated) ;;
    *) echo "usage: $0 [normal|violated] [--ifs-only]" >&2; exit 2 ;;
esac
IFS_ONLY=0
[ "${2:-}" = "--ifs-only" ] && IFS_ONLY=1

ROOT=$(cd "$(dirname "$0")/../." && pwd)          # build/cordon/target
CORDON=$(cd "$ROOT/.." && pwd)                    # build/cordon
if [ -z "${SDP:-}" ]; then
    if [ -n "${QNX_HOST:-}" ]; then
        SDP=$(cd "$QNX_HOST/../../.." && pwd)
    else
        SDP=/workspace/qnx-env/qnx800
    fi
fi
WORK=${WORK:-$CORDON/out/work-$MODE}
BUILD=$ROOT/build
OUT=$CORDON/out/g2-$MODE

set +u
# shellcheck disable=SC1090
. "$SDP/qnxsdp-env.sh" >/dev/null 2>&1
set -u
# Two helpers live outside the SDP on the machine this was written on: a shim
# for awk, and a scratch directory outside the size limited /tmp. Use them when
# they are there and leave the environment alone when they are not.
if [ -d /workspace/qnx-env/bin ]; then
    export PATH=/workspace/qnx-env/bin:$PATH
fi
if [ -d /workspace/qnx-env/tmp ]; then
    export TMPDIR=/workspace/qnx-env/tmp
fi

echo "== compile"
CC_ARGS="-Wall -Wextra -O1"
qcc $CC_ARGS -o "$BUILD/cordon"           "$ROOT/resmgr/cordon.c"             -lsecpol -lslog2 || exit 3
qcc $CC_ARGS -o "$BUILD/cordon-supervise" "$ROOT/supervise/cordon-supervise.c" -lsecpol -lslog2 || exit 3
qcc $CC_ARGS -o "$BUILD/cordon-client"    "$ROOT/client/cordon-client.c"      -lsecpol || exit 3
qcc $CC_ARGS -o "$BUILD/cordon-view"      "$ROOT/client/cordon-view.c"        -lsecpol || exit 3

echo "== check policy"
secpolcompile -o "$BUILD/secpol.bin" "$ROOT/secpol/cordon.policy" || exit 4

echo "== mkqnximage project: $WORK"
mkdir -p "$WORK/local/snippets"
cat > "$WORK/local/snippets/ifs_files.custom" <<EOF
# local/snippets/ifs_files.custom
# Cordon G2 payload: the guarded service, its supervisor, the two clients and
# the scenario runner, all in the boot image. secpol and secpolpush are added
# here because --policy only compiles the policy into the image; it does not add
# the utilities that push it (mkqnximage adds them only for its own --secpol
# modes, which this image does not use).
[uid=0 gid=0 perms=0755] /proc/boot/cordon=$BUILD/cordon
[uid=0 gid=0 perms=0755] /proc/boot/cordon-supervise=$BUILD/cordon-supervise
[uid=0 gid=0 perms=0755] /proc/boot/cordon-client=$BUILD/cordon-client
[uid=0 gid=0 perms=0755] /proc/boot/cordon-view=$BUILD/cordon-view
[uid=0 gid=0 perms=0755] /proc/boot/cordon-boot.sh=$ROOT/boot/cordon-boot.sh
[uid=0 gid=0 perms=0755] /proc/boot/secpolpush=bin/secpolpush
[uid=0 gid=0 perms=0755] /proc/boot/secpol=bin/secpol
EOF

cat > "$WORK/local/snippets/post_start.custom" <<EOF
# local/snippets/post_start.custom
# Cordon G2: run the scenario suite at the end of startup.
/proc/boot/cordon-boot.sh $MODE
EOF

: > "$WORK/local/snippets/ifs_start.custom"
: > "$WORK/local/snippets/system_files.custom"

# The image generates its sshd host keys on first boot when they are missing.
# Under TCG on the e2-micro that costs minutes, and sshd is not part of what
# this gate tests, so an RSA host key is generated here once and shipped in the
# data partition. ed25519 generation is quick and is left to the boot.
KEYDIR="$WORK/local/misc_files"
mkdir -p "$KEYDIR"
if [ ! -s "$KEYDIR/ssh_host_rsa_key" ]; then
    openssl genrsa -traditional -out "$KEYDIR/ssh_host_rsa_key" 2048 2>/dev/null || \
        echo "warning: no RSA host key generated; first boot will be slower" >&2
    chmod 600 "$KEYDIR/ssh_host_rsa_key" 2>/dev/null || true
fi
cat > "$WORK/local/snippets/data_files.custom" <<EOF
# local/snippets/data_files.custom
# sshd host key, pre-generated so the boot does not spend minutes on it.
[uid=15 gid=15 perms=400] var/ssh/ssh_host_rsa_key=$KEYDIR/ssh_host_rsa_key
EOF

echo "== mkqnximage --build ($MODE)"
cd "$WORK" || exit 5
mkdir -p output
if [ "$IFS_ONLY" = 1 ]; then
    # Fast path while iterating: rebuild only the IFS from the build file
    # mkqnximage already generated. The disk image stays byte-identical.
    mkifs output/build/ifs.build output/ifs.bin || exit 6
else
    mkqnximage --build --force --noprompt \
        --proc=procnto-smp-instr \
        --policy="$ROOT/secpol/cordon.policy" \
        --hostname=cordon-g2 || exit 6
fi

mkdir -p "$OUT"
cp output/ifs.bin output/disk-qemu "$OUT/"
{
    echo "mode: $MODE"
    echo "built: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "sdp: $SDP"
    echo "policy: target/secpol/cordon.policy sha256 $(sha256sum "$ROOT/secpol/cordon.policy" | cut -d' ' -f1)"
    echo "binaries:"
    for b in cordon cordon-supervise cordon-client cordon-view; do
        echo "  $b $(sha256sum "$BUILD/$b" | cut -d' ' -f1) $(stat -c %s "$BUILD/$b") bytes"
    done
    echo "images:"
    echo "  ifs.bin $(sha256sum "$OUT/ifs.bin" | cut -d' ' -f1) $(stat -c %s "$OUT/ifs.bin") bytes"
    echo "  disk-qemu $(sha256sum "$OUT/disk-qemu" | cut -d' ' -f1) $(stat -c %s "$OUT/disk-qemu") bytes"
} > "$OUT/build-record.txt"

echo "--- $OUT/build-record.txt"
cat "$OUT/build-record.txt"
```

Build both images:

```bash
SDP=${QNX_HOST%/host/*} make images
```

`make images` calls the script twice and writes `out/g2-normal` and
`out/g2-violated`, each with `ifs.bin`, `disk-qemu` and a `build-record.txt`
that lists the sha256 of every binary and image. If `QNX_HOST` is not set the
script falls back to `/workspace/qnx-env/qnx800`, which is where the SDP lives
on the machine this codelab was written on; set `SDP` to your own install when
it lives somewhere else.

## Capture Both Consoles and Judge Them
Duration: 10:00

Boot each image and keep the console. `tee` is the capture command, and the
file it writes is the input the host gate reads:

```bash
mkdir -p ~/cordon-lab/capture
cd ~/cordon-lab/capture

qemu-system-x86_64 -cpu max -m 384 -snapshot -nographic \
  -drive file=/path/to/cordon/out/g2-normal/disk-qemu,format=raw,if=ide \
  -kernel /path/to/cordon/out/g2-normal/ifs.bin 2>&1 | tee normal-console.log

qemu-system-x86_64 -cpu max -m 384 -snapshot -nographic \
  -drive file=/path/to/cordon/out/g2-violated/disk-qemu,format=raw,if=ide \
  -kernel /path/to/cordon/out/g2-violated/ifs.bin 2>&1 | tee violated-console.log
```

Each boot ends with `CORDON-G2-COMPLETE` on the console. Leave QEMU with
`Ctrl-a` then `x`; the log already holds the whole run. On a host without KVM
the boot runs under pure software emulation, so give it time before you decide
that it has hung.

Now judge both logs on the host. `cordon run` applies one rule per scenario to
the `CORDON-OBS` lines and writes a run directory, and `cordon bundle` copies
the five evidence files out of it:

```bash
cd /path/to/cordon
host/cordon run --console-normal ~/cordon-lab/capture/normal-console.log \
  --console-negative ~/cordon-lab/capture/violated-console.log \
  --out out/g2-run --gate
host/cordon bundle out/g2-run out/g2-bundle
host/cordon claims out/g2-bundle
```

`cordon gate` exits 0 when every scenario carries the result its claim expects,
including the negative control, which the ledger expects to fail. Anything else
exits 1 and names the scenario that disagreed. The bundle holds the five
evidence files: `scenarios.json`, `console.log`, `slog2.txt`, `image.json` and
`sources.json`.

The last piece is the release note. A draft can come from a template or from a
language model; both go through the same verifier, which checks every sentence
against the bundle and deletes the ones the evidence does not support:

```bash
host/cordon note out/g2-bundle --out out/g2-note
```

A sentence that names a scenario the run did not produce, quotes a hash that is
not in the image record, or states a result the bundle does not carry is
removed and listed in `dropped.md`. A sentence that states a performance result
rejects the whole note, because the non-commercial license restricts publishing
evaluation results.

## Clean Up
Duration: 2:00

Remove the lab images and the capture when you are done:

```bash
rm -rf ~/cordon-lab
```

If you pushed a policy inside a running guest and want the system back to its
default behaviour, stop QEMU. The policy lives in the kernel until the next
boot.

## What Next
Duration: 2:00

You now have the four pieces that most QNX services need: a path, a policy, a
supervisor and a boot image, plus a host gate that reads the console instead of
trusting the target. Three directions to take it further:

- Add a second guarded path in a different zone and give each zone its own
  permission class, so one policy file expresses per-zone access.
- Replace the supervisor with a HAM entity once the optional package is
  installed, and keep the same observation lines so the gate does not change.
- Add a scenario that covers a client killed in the middle of a write, and
  check that the event log never records a partial record.
