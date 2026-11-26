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
