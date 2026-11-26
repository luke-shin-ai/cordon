/*
 * cordon-view. The status consumer of /dev/cordon.
 *
 * This is the process the kill-screen scenario terminates. It is NOT the QNX
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
