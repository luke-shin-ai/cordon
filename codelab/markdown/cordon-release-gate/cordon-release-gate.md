id: cordon-release-gate
title: Build a Guarded Device Node on QNX 8.0 and Verify It Under QEMU
summary: Write a resource manager at /dev/cordon, protect it with a secpol policy, keep it alive with a supervisor, boot the result in QEMU, and judge the image from the host with a pass or fail gate.
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

In this codelab you build a small but complete QNX 8.0 service that owns one
path in the pathname space, `/dev/cordon`. Four things are wired together:

- a resource manager that answers a status read and records write events
- a security policy that decides which process may read and which may write
- a supervisor that starts the service again after it dies
- a boot image plus a host gate that turns the run into PASS or FAIL

The point is not the service itself. The point is the last step: an image is
released only when every check the verifier expects has a recorded verdict.
Nothing in this codelab measures time, rate or resource use. Every
decision is a true or false question: did the answer arrive, which errno came
back, did the sequence number change.

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
source ~/qnx800/qnxsdp-env.sh
qcc -V
which mkqnximage secpolcompile
qemu-system-x86_64 --version
```

`qcc -V` prints the target list. The default target for this codelab is
`gcc_ntox86_64`, which matches the QEMU x86_64 image you build in the next
step.

## Build and Boot a QNX 8.0 Image
Duration: 8:00

`mkqnximage` is part of the SDP and builds a bootable QEMU image from your own
file list. It works inside the directory it is started in: the project lives
there, and the build writes its output beside the project.

```bash
mkdir -p ~/cordon-lab/base && cd ~/cordon-lab/base
mkqnximage --type=qemu --arch=x86_64 --hostname=cordon --noprompt
```

The build ends with `ifs.bin` and `disk-qemu` in `output/`. Boot the stock image
once, before adding anything of your own, so you know the base is good. Pass the
IFS as the kernel and attach the disk:

```bash
qemu-system-x86_64 -cpu max -m 384 -snapshot -nographic \
  -drive file=$PWD/output/disk-qemu,format=raw,if=ide \
  -kernel $PWD/output/ifs.bin
```

Two details matter on QEMU. Without `-cpu max` the QNX kernel may stop early
on a CPUID check and print nothing. The image is a multiboot IFS, so pass it
with `-kernel` and also attach the disk.

You are in when the console prints a QNX banner and `Startup complete`, and the
guest shell answers:

```bash
uname -a
```

Keep this shell open. The guest has no file share with the host, so a program
reaches the target one way: it is added to the boot image on the host, and it
runs from `/proc/boot` when that image boots. Every program in this lab follows
that path. You build it on the host into `build/`, the image step at the end
lists it in `local/snippets/ifs_files.custom`, and the boot starts it.

## Write the Resource Manager
Duration: 12:00

A QNX resource manager registers a path, then receives messages for it. The
kernel routes a client's `open`, `read` and `write` to your handlers. Save this
as `cordon.c` on the host.

```c
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <sys/dispatch.h>
#include <sys/iofunc.h>
#include <sys/resmgr.h>
#include <sys/slog2.h>

static iofunc_attr_t attr;
static int events;
static char status[256];

/* One line per fact. The host decides what the line means. */
static void emit(const char *kind, const char *detail)
{
    printf("CORDON-OBS service=%s %s\n", kind, detail);
    fflush(stdout);
}

static int cordon_read(resmgr_context_t *ctp, io_read_t *msg, RESMGR_OCB_T *ocb)
{
    iofunc_ocb_t *o = (iofunc_ocb_t *)ocb;
    int n;

    if (iofunc_read_verify(ctp, msg, ocb, NULL) != EOK) {
        return EACCES;
    }
    n = snprintf(status, sizeof status, "pid=%d events=%d\n", (int)getpid(), events);
    if (o->offset >= n) {
        _IO_SET_READ_NBYTES(ctp, 0);
        return _RESMGR_NPARTS(0);
    }
    SETIOV(ctp->iov, status + o->offset, n - o->offset);
    _IO_SET_READ_NBYTES(ctp, n - o->offset);
    o->offset = n;
    return _RESMGR_NPARTS(1);
}

static int cordon_write(resmgr_context_t *ctp, io_write_t *msg, RESMGR_OCB_T *ocb)
{
    char record[128];
    int n = msg->i.nbytes;

    if (iofunc_write_verify(ctp, msg, ocb, NULL) != EOK) {
        return EACCES;
    }
    if (n <= 0 || n >= (int)sizeof record) {
        return EINVAL;
    }
    if (resmgr_msgread(ctp, record, n, sizeof msg->i) != n) {
        return EFAULT;
    }
    record[n] = '\0';
    events++;
    emit("accept", record);
    _IO_SET_WRITE_NBYTES(ctp, msg->i.nbytes);
    return _RESMGR_NPARTS(0);
}

int main(void)
{
    dispatch_t *dpp;
    resmgr_attr_t rattr;
    resmgr_connect_funcs_t connect;
    resmgr_io_funcs_t io;
    dispatch_context_t *ctp;

    dpp = dispatch_create();
    memset(&rattr, 0, sizeof rattr);
    rattr.nparts_max = 1;
    rattr.msg_max_size = 2048;
    iofunc_func_init(_RESMGR_CONNECT_NFUNCS, &connect, _RESMGR_IO_NFUNCS, &io);
    iofunc_attr_init(&attr, S_IFCHR | 0666, NULL, NULL);
    io.read = cordon_read;
    io.write = cordon_write;

    if (resmgr_attach(dpp, &rattr, "/dev/cordon", _FTYPE_ANY, 0,
                      &connect, &io, &attr) == -1) {
        perror("resmgr_attach");
        return 1;
    }
    emit("up", "path=/dev/cordon");

    ctp = dispatch_context_alloc(dpp);
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

Build it on the host. The image carries the binary as `/proc/boot/cordon`:

```bash
qcc -Wall -Wextra -O1 -o build/cordon cordon.c
```

`resmgr_attach` is the call that makes `/dev/cordon` appear. `iofunc_read_verify`
and `iofunc_write_verify` apply the default permission model; the policy step
replaces that default with an explicit question to secpol.

Once the image step below carries this binary, a boot starts it and the path
exists in the guest:

```bash
/proc/boot/cordon &
ls -l /dev/cordon
```

## Talk to the Node From a Client
Duration: 6:00

A tiny client makes the two operations visible. Save it as `client.c`.

```c
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    char buf[256];
    int fd, n;

    if (argc < 2) {
        return 2;
    }
    if (strcmp(argv[1], "status") == 0) {
        fd = open("/dev/cordon", O_RDONLY);
        if (fd < 0) {
            printf("CORDON-OBS client op=status result=ERR errno=%d\n", errno);
            return 1;
        }
        n = read(fd, buf, sizeof buf - 1);
        if (n < 0) {
            printf("CORDON-OBS client op=status result=ERR errno=%d\n", errno);
            return 1;
        }
        buf[n] = '\0';
        printf("CORDON-OBS client op=status result=OK status=%s", buf);
        close(fd);
        return 0;
    }
    if (strcmp(argv[1], "write") == 0) {
        fd = open("/dev/cordon", O_WRONLY);
        if (fd < 0) {
            printf("CORDON-OBS client op=open result=ERR errno=%d\n", errno);
            return 1;
        }
        if (write(fd, "zone=main", 9) < 0) {
            printf("CORDON-OBS client op=write result=ERR errno=%d\n", errno);
            return 1;
        }
        printf("CORDON-OBS client op=write result=OK\n");
        close(fd);
        return 0;
    }
    return 2;
}
```

Build it on the host; the image carries it as `cordon-client`, and the two
operations are `status` and `write`:

```bash
qcc -Wall -Wextra -O1 -o build/cordon-client client.c
```

The status read returns the service pid and the event count. The write adds one
event. Until the policy step, nothing is secured: any process that can open the
path can write to it. The boot image step runs both operations from the console,
so you see the answer for each.

## Add a Security Policy
Duration: 12:00

`secpol` decides access in the kernel and in the service. A policy file names
types, custom permission classes and the rules between them. Save this as
`cordon.policy`.

```text
type cordon_t;
type cordon_writer_t;
type cordon_probe_t;

class cordon { write status };

allow_attach cordon_t /dev/cordon;
allow cordon_t self:ability { spawn };
allow { cordon_writer_t cordon_probe_t } cordon_t : channel connect;
allow { cordon_writer_t cordon_probe_t } cordon_t : cordon status;
allow cordon_writer_t cordon_t : cordon write;
```

Three layers are in play:

1. `allow_attach` decides which process may put a channel at the path.
2. `channel connect` decides which type may reach the service at all. A type
   with no rule is refused by the kernel before your code runs.
3. The custom class `cordon` decides the two operations your handlers perform.
   The service asks the policy on every read and write instead of trusting
   itself.

Compile the policy into the build directory the image step copies from:

```bash
secpolcompile -o build/secpol.bin cordon.policy
```

At boot the image pushes that file and starts each program under an explicit
type. These are the startup lines the image step carries:

The probe is refused and the writer succeeds. A refusal from the policy check
inside the service comes back as `errno=13` (`EACCES`). A client type with no
connect rule never reaches the service and gets `errno=1` (`EPERM`) from the
kernel.

Two details cost time if you miss them:

- `secpol_get_type_id()` returns the number your own channel carries. Rules are
  written against `cordon_t`, so the service must resolve that name at startup
  and pass the number, not 0, to `secpol_check_permission`. Passing 0 matches
  nothing and denies every request.
- `secpol_type_id()` in a client is only accurate for processes that change type
  with `secpol_transition_type()`. A process started with `on -T` reports 0.
  The type the service sees arrives in the message it received, so log that.

To make the service consult the policy, add the check to both handlers:

```c
static secpol_permission_t *perm_write;
static uint32_t object_type;

/* in main, after secpol_open(NULL, SECPOL_USE_AS_DEFAULT) */
object_type = secpol_get_type_id(NULL, "cordon_t");
perm_write = secpol_get_permission(NULL, "cordon", "write", 0);

/* in cordon_write, before reading the message */
if (perm_write == NULL ||
    secpol_check_permission(&ctp->info, object_type, perm_write) != 0) {
    emit("deny", "kind=write result=DENY check=EPERM");
    return EACCES;
}
```

## Survive a Process Death
Duration: 10:00

A service that dies takes its path with it. On QNX the High Availability
Manager can restart a process, but it is an optional package that a base SDP
install does not include. The primitives it drives are in libc, so a
supervisor is short: `posix_spawn`, `waitpid`, and a sequence number that the
service reads at startup.

```c
#include <spawn.h>
#include <stdio.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;

int main(int argc, char **argv)
{
    int seq = 0, max = 4;

    while (seq < max) {
        pid_t pid;
        int status = 0;
        int signal = 0;
        char number[16];
        char *args[] = { argv[1], "-r", number, NULL };

        seq++;
        snprintf(number, sizeof number, "%d", seq);
        if (posix_spawn(&pid, argv[1], NULL, NULL, args, environ) != 0) {
            printf("CORDON-OBS supervise spawn=ERR seq=%d\n", seq);
            return 1;
        }
        printf("CORDON-OBS supervise spawn seq=%d pid=%d\n", seq, (int)pid);
        fflush(stdout);
        waitpid(pid, &status, 0);
        if (WIFSIGNALED(status)) {
            signal = WTERMSIG(status);
        }
        printf("CORDON-OBS supervise exit seq=%d signal=%d\n", seq, signal);
        fflush(stdout);
        printf("CORDON-OBS supervise respawn next_seq=%d\n", seq + 1);
        fflush(stdout);
    }
    return 0;
}
```

Start the service through the supervisor and kill the child from another shell.
The console shows the exit, the respawn and a new service start with the next
sequence number. The service prints its sequence in the status read, so a
client can tell the difference between the service that died and the one that
replaced it.

```text
on -T cordon_t /proc/boot/cordon-supervise /proc/boot/cordon &
sleep 1
kill -9 $(pidin -F "%N %p" | awk '/cordon$/ {print $2}')
on -T cordon_writer_t /proc/boot/cordon-client status
```

## Put It in the Boot Image
Duration: 10:00

Now move the pieces into the image so a boot produces the whole run. Copy the
stock project twice, add the payload to each image file list, and write the
startup lines into each project's `local/snippets/post_start.custom`.

```bash
LAB=$HOME/cordon-lab
SDP_TARGET=${QNX_TARGET:?source qnxsdp-env.sh first}/x86_64
cd $LAB
cp -r base normal
cp -r base broken
for d in normal broken; do
  for f in cordon cordon-client cordon-supervise; do
    echo "[uid=0 gid=0 perms=0755] /proc/boot/$f=$LAB/build/$f" >> $d/local/snippets/ifs_files.custom
  done
  echo "[uid=0 gid=0 perms=0755] /proc/boot/secpol.bin=$LAB/build/secpol.bin" >> $d/local/snippets/ifs_files.custom
  echo "[uid=0 gid=0 perms=0755] /proc/boot/secpolpush=$SDP_TARGET/bin/secpolpush" >> $d/local/snippets/ifs_files.custom
  echo "[uid=0 gid=0 perms=0755] /proc/boot/secpol=$SDP_TARGET/bin/secpol" >> $d/local/snippets/ifs_files.custom
done
```

The startup lines for the normal image. It pushes the policy, starts the
supervisor, waits for the path, and runs the clients in a fixed order:

```bash
cat >> normal/local/snippets/post_start.custom <<'EOF'
secpolpush
on -T cordon_t /proc/boot/cordon-supervise /proc/boot/cordon &
while [ ! -e /dev/cordon ]; do sleep 1; done
on -T cordon_probe_t /proc/boot/cordon-client write
on -T cordon_writer_t /proc/boot/cordon-client write
echo CORDON-NORMAL-COMPLETE
EOF
```

The second image differs only in the order: the first client runs before the
service exists.

```bash
cat >> broken/local/snippets/post_start.custom <<'EOF'
secpolpush
on -T cordon_probe_t /proc/boot/cordon-client write
echo CORDON-BROKEN-FIRST-CLIENT
on -T cordon_t /proc/boot/cordon-supervise /proc/boot/cordon &
while [ ! -e /dev/cordon ]; do sleep 1; done
on -T cordon_writer_t /proc/boot/cordon-client write
echo CORDON-BROKEN-COMPLETE
EOF
```

Build both, then boot them the same way you booted the stock image, pointing at
each `output/`:

```bash
cd $LAB/normal && mkqnximage --build --force --noprompt
cd $LAB/broken && mkqnximage --build --force --noprompt
```

Both images boot. In the broken image the first client fails with
`errno=2` (`ENOENT`) because `/dev/cordon` is not there yet, and the same
client succeeds after the service starts. That failure is the evidence that
the order matters. An image that starts the service first must never show it.

## Judge the Image From the Host
Duration: 6:00

The target prints facts. The host decides. A gate reads the observations from a
serial console, applies a rule per scenario, and returns one exit code. The
companion repository for this codelab carries such a gate; clone it beside your
lab and point it at the two logs:

```bash
git clone https://github.com/luke-shin-ai/cordon.git
host/cordon run --console-normal normal-console.log \
  --console-negative broken-console.log --out run --gate
host/cordon bundle run bundle
```

`cordon gate` exits 0 only when every scenario matched its expectation,
including the negative control, which must FAIL. Anything else exits 1 and
names the scenario that disagreed.

The last piece is the release note. A draft can come from a template or from a
language model; both go through the same verifier, which checks every sentence
against the bundle and deletes the ones the evidence does not support:

```bash
host/cordon note bundle --out note
```

A sentence that names a scenario the run did not produce, quotes a hash that is
not in the image record, or reports a pass where the bundle recorded a fail is
removed and listed in `dropped.md`. A sentence that states a performance
result rejects the whole note, because the non-commercial license restricts
publishing evaluation results.

## Clean Up
Duration: 2:00

Stop the supervisor and the service, and remove the lab images when you are
done:

```bash
kill $(pidin -F "%N %p" | awk '/cordon/ {print $2}')
rm -rf ~/cordon-lab
```

If you pushed a policy and want the system back to its default behaviour,
reboot the guest. The policy lives in the kernel until the next boot.

## What Next
Duration: 2:00

You now have the four pieces that most QNX services need: a path, a policy, a
supervisor and a boot image. Three directions to take it further:

- Add a second guarded path in a different zone and give each zone its own
  permission class, so one policy file expresses per-zone access.
- Replace the supervisor with a HAM entity once the optional package is
  installed, and keep the same observation lines so the gate does not change.
- Add a scenario that covers a client that is killed in the middle of a write,
  and check that the event log never records a partial record.
