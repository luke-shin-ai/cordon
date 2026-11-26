/*
 * /dev/cordon resource manager. QNX 8.0, x86_64.
 *
 * STATUS: skeleton, not built. The build environment has no SDP 8.0 install
 * yet (myQNX credential needed for the QNX package mirror), so this file has
 * never been compiled by qcc. Do not cite it as working code.
 *
 * Contract: docs/design.md section 1. Claims C1 to C4 write through this path.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/iofunc.h>
#include <sys/dispatch.h>

/* TODO(G1): attach /dev/cordon and answer the status query used by the
 * kill-screen scenario. Deliberately unimplemented until the toolchain exists. */

int main(int argc, char *argv[])
{
    (void)argc;
    (void)argv;
    fprintf(stderr, "cordon: skeleton, no resource manager attached yet\n");
    return 1;
}
