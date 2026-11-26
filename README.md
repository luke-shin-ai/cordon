# Cordon

A release decision gate for QNX images. Cordon runs a fixed set of verification
scenarios against a QNX 8.0 target and answers one question: does this image
pass, or does it not.

Status 2026-11-26: the target side is built and runs. `/dev/cordon` is a real
resource manager, a secpol policy decides who may read and write it, a
supervisor starts the guarded service again after it dies, and two images (one
with the intended startup order, one with the client before the service) were
booted under QEMU on an ordinary cloud VM. The six scenarios were judged from
that run: gate PASS, 6 of 6 as expected. See `docs/design.md` for what is
guaranteed and what is not.

## What it does

Cordon is not a tracing tool and not a profiler. It does not publish latency or
throughput numbers. It records whether each of six scenarios passed or failed,
with the raw console evidence kept in a bundle, and refuses to produce a release
note that claims more than the evidence supports.

The target asserts nothing. It prints one `CORDON-OBS` line per observed fact
and the host decides. A PASS is something the host concluded, not something the
target announced.

## Layout

| Path | Contents |
|---|---|
| `target/resmgr/cordon.c` | resource manager that owns `/dev/cordon` (QNX 8.0 x86_64) |
| `target/secpol/cordon.policy` | security policy: attach, channel connect, and a custom `write`/`status` class |
| `target/supervise/cordon-supervise.c` | starts the guarded service and starts it again after it dies |
| `target/client/cordon-client.c` | one operation per run, one observation line |
| `target/client/cordon-view.c` | the long-lived status consumer the kill-screen scenario terminates |
| `target/boot/cordon-boot.sh` | the scenario runner that runs at the end of startup |
| `target/boot/build-g2-image.sh` | builds the normal and the misordered image with mkqnximage |
| `host/cordon` | host CLI: `claims`, `run`, `gate`, `bundle` |
| `claims.yaml` | the six claims, each with its scenario, evidence source and status |
| `docs/design.md` | scenario contract, PASS/FAIL rules, how the target is built, guaranteed and not guaranteed |

## Build and run

Cross build (needs QNX SDP 8.0; `qcc` must be on PATH or `QNX_HOST` set):

```
qcc -o cordon target/resmgr/cordon.c -lsecpol -lslog2
qcc -o cordon-supervise target/supervise/cordon-supervise.c -lsecpol -lslog2
qcc -o cordon-client target/client/cordon-client.c -lsecpol
qcc -o cordon-view target/client/cordon-view.c -lsecpol
```

Images (mkqnximage, part of the SDP):

```
target/boot/build-g2-image.sh normal      # service first
target/boot/build-g2-image.sh violated    # first client before the service
```

Judging a run. `cordon run` boots the image through `CORDON_BOOT_CMD`, a program
called as `CMD <imageDir> <consoleLogPath>`; with none set it uses
`qemu-system-x86_64` on the local machine. The two images are booted one after
the other:

```
CORDON_BOOT_CMD=./boot-image.sh host/cordon run out/g2-normal \
    --negative out/g2-violated --out out/g2-run --gate
```

A console log captured earlier can be judged without booting anything:

```
host/cordon run --console-normal out/g2-run/console-normal.log \
    --console-negative out/g2-run/console-negative.log --out out/g2-run --gate
```

Then:

```
host/cordon bundle out/g2-run out/g2-bundle    # 5 evidence files
host/cordon claims out/g2-bundle               # ledger against the run
```

Host tests need no QNX target: `make host-test`.

## Scope line

Fixed on purpose, so it stays finishable: one resource manager, six scenarios,
one security policy file, one supervisor, four CLI commands, one codelab.
Nothing beyond this line is added before the hackathon submission.

## License

Apache License 2.0, see `LICENSE`.
