# Cordon

A release decision gate for QNX images. Cordon runs a fixed set of verification
scenarios against a QNX 8.0 target and answers one question: does this image
pass, or does it not.

Status 2026-11-26: the target side is built and runs on QNX 8.0. `/dev/cordon`
is a resource manager, a secpol policy decides who may read and write it, a
supervisor starts the guarded service again after it dies, and two images (one
with the intended startup order, one with a client before the service) were
booted under QEMU on an ordinary cloud VM. The six scenarios were judged from
that run: gate PASS, six of six as expected, including the negative control.
The AI layer drafts a release note from the bundle and deletes every sentence
the bundle does not support. See `docs/design.md` for what is guaranteed and
what is not.

## What it does

Cordon is not a tracing tool and not a profiler. It does not publish
measurements of any kind. It records whether each of six scenarios passed or
failed, keeps the raw console as the evidence behind each verdict, and refuses
to produce a release note that claims more than the evidence supports.

The target asserts nothing. It prints one `CORDON-OBS` line per observed fact
and the host decides. A PASS is something the host concluded from those lines,
not something the target announced.

| Command | Input | Output |
|---|---|---|
| `cordon run` | an image directory, or two captured console logs | `scenarios.json` with one verdict per scenario |
| `cordon gate` | a bundle | exit code 0 or 1, and the name of each scenario that disagreed |
| `cordon bundle` | a run directory | one directory with the five evidence files |
| `cordon note` | a bundle, and optionally a draft | a release note with unsupported sentences removed |
| `cordon claims` | a bundle | the claim ledger with the verdict each claim earned |

## QNX 8.0 pieces and where they are

Every row below is QNX specific: a QNX library, a QNX mechanism, or a QNX build
step. The file and function are named so a reviewer can open them.

| QNX mechanism | File | Function or call |
|---|---|---|
| Resource manager | `target/resmgr/cordon.c` | `main` calls `dispatch_create`, `iofunc_func_init` and `resmgr_attach` for `/dev/cordon`; `cordon_read` and `cordon_write` are the handlers |
| Native message passing | `target/resmgr/cordon.c` | `dispatch_block` and `dispatch_handler` receive the client messages; `resmgr_msgread` reads the write payload from the sender |
| Security policy | `target/secpol/cordon.policy` | types `cordon_t`, `cordon_writer_t`, `cordon_probe_t`, `cordon_view_t`, `cordon_outsider_t`; class `cordon { write status }`; `allow_attach`, `channel connect` |
| Policy enforcement | `target/resmgr/cordon.c` | `secpol_get_type_id`, `secpol_get_permission` and `secpol_check_permission` on every read and write |
| Policy load at run time | `target/boot/cordon-boot.sh` | `secpolpush`, then `secpol -Q` as the record of the policy id in force |
| Startup and boot customization | `target/boot/buildfile`, `target/boot/cordon-boot.sh` | the startup order contract, and the scenario runner appended to `post_startup.sh` by `target/boot/build-g2-image.sh` |
| Process supervision | `target/supervise/cordon-supervise.c` | `main` calls `posix_spawn` and `waitpid`, records each spawn and exit, and starts the service again under a new sequence number |
| Logging | `target/resmgr/cordon.c`, `target/supervise/cordon-supervise.c` | `slog2_register` and `slog2f`, dumped on the target with `slog2info -S` |
| Cross build and image | `Makefile`, `target/boot/build-g2-image.sh` | `qcc` for four binaries, `secpolcompile` for the policy, `mkqnximage` for the image |
| Client side | `target/client/cordon-client.c`, `target/client/cordon-view.c` | `main`, `do_status`, `do_write`, `do_open`; the view is the long lived consumer |

## Build and run

Requirements: QNX SDP 8.0 on a Linux host (the SDP 8.0 host tools do not run
on macOS), `qcc` 12.2.0 with the `gcc_ntox86_64` target, `mkqnximage`,
`secpolcompile`, and `qemu-system-x86_64` on the host. The target is QEMU
x86_64; no board is needed.

```bash
source ~/qnx800/qnxsdp-env.sh
make target          # four binaries with qcc -Wall -Wextra, and the compiled policy
make images          # normal image, and the image with the client before the service
```

`make images` writes both images under `out/`. Boot and judge them:

```bash
CORDON_BOOT_CMD=./boot-image.sh host/cordon run out/g2-normal \
    --negative out/g2-violated --out out/g2-run --gate
```

`CORDON_BOOT_CMD` is a program called as `CMD <imageDir> <consoleLogPath>`; with
none set, `cordon run` uses `qemu-system-x86_64` on the local machine. A console
captured earlier can be judged without booting anything:

```bash
host/cordon run --console-normal out/g2-run/console-normal.log \
    --console-negative out/g2-run/console-negative.log --out out/g2-run --gate
host/cordon bundle out/g2-run out/g2-bundle
host/cordon claims out/g2-bundle
```

Then the release note:

```bash
host/cordon note out/g2-bundle --out out/g2-note
```

`cordon note` writes the draft it received, the note that survived, a list of
removed sentences with reasons, and the whole check as JSON, all in the output
directory. A sentence survives only when every
checkable claim in it is in the bundle: a scenario name and its verdict, a
sha256 from `image.json`, the commit from `sources.json`, an errno from the
observation lines, or a count the bundle can confirm. A sentence with no
checkable claim is removed. A sentence that states a measurement rejects the
whole note.

The default drafter is a template and needs no network, so the same bundle
always produces the same note. `--provider proxy` sends the bundle facts to an
OpenAI compatible endpoint named by `CORDON_LLM_URL`, with `CORDON_LLM_KEY` and
`CORDON_LLM_MODEL`; the answer goes through the same verifier, so a model can
only make the note shorter, never more generous. The recorded evidence for this
repository was produced with the template provider, offline.

## Evidence bundle

| File | Content |
|---|---|
| `scenarios.json` | per scenario: name, verdict, the rule applied, the observation lines it was decided from |
| `console.log` | serial console of both images |
| `slog2.txt` | the target's slog2 dump, carried out through the console |
| `image.json` | sha256 of the IFS and disk actually booted, and the QNX version string |
| `sources.json` | git commit of the repository and the `qcc -V` target list |
| `manifest.json` | the file list, and whether the bundle is complete |

The bundle is the only input the gate and the note layer may read.

## What a PASS means, and what it does not

A PASS bundle means the six scenarios were executed on the image whose hashes
are in `image.json`, and their observations were as stated. The negative control
is part of that: an image whose first client starts before the service must earn
FAIL, and the gate expects FAIL for it. If such an image ever earned PASS, the
gate would fail, because then the control proved nothing. The gate trusts the
console it is given: it does not yet sign a bundle or prove cryptographically
that a console came from the image named in `image.json`.

It does not mean the image is correct outside these six scenarios, that it
meets any safety certification, or anything about speed, rate or resource use.
Section 4.6(i) of the QNX Development License Agreement (Non-Commercial)
restricts publishing the results of performance or functional evaluation, so no
figures about speed, time, memory or load appear in this repository, in the
submission text or in the video. A verdict here says only whether a documented
scenario behaved as specified. Timestamps in a slog2 dump are event labels, not
measurements.

Two substitutions are recorded here rather than hidden in a caption:

- **HAM.** QNX 8.0 ships the High Availability Manager as an optional package
  (`com.qnx.qnx800.target.os_services.ham`), and the installed baseline does
  not include it: there is no `ham` binary, no `libham` and no header under
  `$QNX_TARGET`, and the software center install path for it needs myQNX
  credentials. Restart is therefore implemented by `cordon-supervise` with
  `posix_spawn`, `waitpid` and slog2, which are the primitives a HAM rule would
  drive. Claim C4 names the supervisor; its note says which package would take
  that role in a production image. Enabling the real HAM is one package install
  and one entity file.
- **Screen process.** The image is built with `--graphics=no`, so there is no
  QNX Screen. The kill-screen scenario terminates `cordon-view`, the long lived
  status consumer, which is the same kind of death: the process that consumes
  the service is gone and the service must keep answering.

## Layout

| Path | Contents |
|---|---|
| `host/cordon` | host CLI: `claims`, `run`, `gate`, `bundle`, `note` |
| `host/cordon_notes.py` | note drafter and the deterministic claim verifier |
| `target/resmgr/cordon.c` | resource manager that owns `/dev/cordon` |
| `target/secpol/cordon.policy` | security policy for the path and the custom class |
| `target/supervise/cordon-supervise.c` | starts the guarded service and starts it again after it dies |
| `target/client/cordon-client.c` | one operation per run, one observation line |
| `target/client/cordon-view.c` | the long lived status consumer the kill-screen scenario terminates |
| `target/boot/cordon-boot.sh` | the scenario runner that runs at the end of startup |
| `target/boot/build-g2-image.sh` | builds the normal and the misordered image with mkqnximage |
| `codelab/markdown/cordon-release-gate/` | the codelab source in the QNX codelabs metadata format |
| `codelab/render.py` | validates the codelab and renders it to static HTML for GitHub Pages |
| `tools/` | style, secret, number and public fact checks used before a release |
| `claims.yaml` | the six claims, each with its scenario, evidence source and status |
| `docs/design.md` | scenario contract, PASS and FAIL rules, and what the run changed |

## Tests

Host tests need no QNX target and no network:

```bash
make host-test     # gate rules, judging rules, note and verifier, codelab format
python3 codelab/render.py --check
```

The same tests run without make:

```bash
python3 tests/test_gate.py
python3 tests/test_run.py
python3 tests/test_note.py
python3 tests/test_codelab.py
```

## Codelab

`codelab/markdown/cordon-release-gate/` builds a guarded device node from an
empty project in about ten steps, and `codelab/render.py --out site` turns it
into a static site that can be published as it is. The same source is what the
submission entry uses.

## Scope line

Fixed on purpose, so it stays finishable: one resource manager, six scenarios,
one security policy file, one supervisor, five CLI commands, one codelab.
Nothing beyond this line is added before the hackathon submission.

## License

Apache License 2.0, see `LICENSE`.
