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

Every code listing in this codelab is taken from the companion repository
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

```text file=target/secpol/cordon.policy
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

```c file=target/resmgr/cordon.c
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

```c file=target/client/cordon-client.c
```

The second one reads the status in a loop and stays alive until its count runs
out. It is the process a death scenario terminates, and it stands in for a
screen or a dashboard that consumes the service:

```c file=target/client/cordon-view.c
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

```c file=target/supervise/cordon-supervise.c
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

```bash file=target/boot/cordon-boot.sh
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

```bash file=target/boot/build-g2-image.sh
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
