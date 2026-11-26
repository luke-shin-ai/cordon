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
