# Cordon design notes

Written 2026-11-26, revised the same day after the first run on QNX 8.0. Section
8 records what that run changed and why.

## 1. Scenario contract

Six scenarios. Each one is a target-side action plus the observations it must
produce. The target prints one line per fact, and `host/cordon run` applies the
rules below to those lines. A scenario's result is
one of two values, and there is no partial credit and no score.

| Scenario | Precondition | Action | Rule |
|---|---|---|---|
| kill-consumer | normal image booted, guarded service running | terminate the status consumer (`cordon-view`) | the consumer is gone and the guarded service still answers a status read |
| deny-unprivileged-write | normal image booted | an unprivileged client writes to /dev/cordon; a client with no policy rule tries to open it | the write is refused with EACCES, the open is refused with EPERM, and the service still answers |
| allow-privileged-write | normal image booted | the permitted client writes to /dev/cordon | the write succeeds and the service event count goes up with the record |
| supervised-restart | normal image booted | terminate the guarded service | a new service process answers with a higher sequence number, and the supervisor recorded the respawn |
| boot-order-violation | image built with the client before the service | boot | the first client finds no service, and the same client succeeds once the service starts |
| all-pass | normal image booted | the startup-order check plus each positive scenario above | the four positive rules hold and the startup-order rule holds |

The negative scenario is inverted on purpose: the rule for it requires the
outcome that a correct image must not produce, so the rule for a misordered
image requires FAIL. `cordon gate` expects FAIL for it (claim C5 has
`expected_result: FAIL`). If a misordered image ever satisfied
the startup-order rule, the gate would fail, because the control would then say
nothing about startup order.

## 2. What the evidence bundle contains

`cordon bundle` writes one directory per run:

| File | Content |
|---|---|
| `scenarios.json` | per scenario: name, result, the rule applied, the observation lines it was decided from |
| `console.log` | serial console of both images, with the boot host's header lines |
| `slog2.txt` | slog2 dump the target printed during the run, carried out through the console |
| `image.json` | sha256 of the IFS and disk actually booted, QNX version string, build record when available |
| `sources.json` | git commit of the repository, `qcc -V` target list |

The bundle is the only input the gate and the AI layer may read.

## 3. Gate rule

`cordon gate` exits 0 when every non-negative scenario in the bundle carries the
result its claim expects and every negative scenario carries the result its
claim expects. Any other combination exits 1 and prints the failed scenario
names. A build system can therefore act on the exit code without parsing text.

## 4. AI layer rule

The AI layer drafts a release note from the bundle only. The drafter is a
template by default, so the same bundle always produces the same text; an
optional provider can send the bundle facts to a language model instead, and
the answer goes through the same check.

A deterministic verifier then reads the draft sentence by sentence. Every
checkable claim in a sentence must be present in the bundle and must agree with
it:

- a scenario name and the result it states, against `scenarios.json`
- a claim id, against `claims.yaml` and the scenario it names
- a sha256, against `image.json` and the console
- a commit, against `sources.json`
- an errno, a policy id or a version string, against the console and slog2 dump
- a count of scenarios, claims, files or images, against the bundle

A sentence with no checkable claim at all is deleted, because the note may not
say what the evidence cannot support. A sentence that states a measurement, a
rate or a share of a whole rejects the whole note, because the license does not
allow publishing evaluation results. Surviving sentences keep the claim id they
were checked against, and every deletion is listed with its reason in
`dropped.md`. Sentences are only ever deleted: whatever drafted the note, the
text that comes out of the verifier is a subset of what went in.

## 5. What a gate result covers, and what it does not

A bundle carries one result per scenario, the observation lines behind each one,
the hashes of the image that was booted, and the commit the image was built
from. The verifier compares a sentence with those records and drops it when they
do not agree.

A bundle does not establish that the image is correct outside these six
scenarios, that it meets any safety certification, or anything about speed, rate
or resource use. The license for the QNX non-commercial toolchain restricts
publishing the results of performance or functional evaluation (section 4.6(i)),
so no such numbers appear in this repository, in the submission text or in the
video, and the verdicts of the runs behind this repository are not published
either. That approval was requested from BlackBerry in writing and has not been
given yet. Timestamps in a slog2 dump are event labels, not measurements.

## 6. How the target is put together

| Piece | File | What it does |
|---|---|---|
| guarded service | `target/resmgr/cordon.c` | attaches `/dev/cordon`, answers a status read, records accepted writes in slog2 and in `/data/var/cordon/events.log` |
| policy | `target/secpol/cordon.policy` | `allow_attach` for the service, `channel connect` for three client types, a custom class `cordon { write status }`, and one type with no rule at all |
| supervisor | `target/supervise/cordon-supervise.c` | starts the service, waits for it, starts it again when it dies, records each spawn and exit |
| clients | `target/client/cordon-client.c`, `cordon-view.c` | one operation per run, one observation line; the view is the long-lived consumer that kill-consumer terminates |
| image | `target/boot/build-g2-image.sh`, `cordon-boot.sh` | builds the normal and the misordered image with mkqnximage and runs the scenarios at the end of startup |

The policy is pushed by the scenario runner (`secpolpush`) rather than from the
IFS start script. Both work; pushing at the start of the run keeps the boot
itself the same as the G1 image, and makes `secpol -Q` at run time the evidence
that a policy is in force. The cost is that services started before the push run
untyped, which is why every process that the scenarios depend on is started
explicitly with `on -T`.

## 7. Open questions settled at the first run

1. **Resource manager shape.** A single-threaded `resmgr_attach` plus dispatch
   loop, with `read` and `write` handlers and `iofunc` defaults for the rest.
2. **Where the startup-order check lives.** In the gate, not in the image build:
   the image only records what happened, and the host decides whether that
   matches the rule. The same script runs in both images with a different start
   order, so the only variable is the order.
3. **Where restart records go.** Into slog2 and the event log by the supervisor
   itself, because the guarded service is dead at that moment and cannot record
   its own restart.

Open: whether the custom permission check should also be applied to `open`
(connect) rather than only to `read` and `write`; today the kernel's channel
connect rule covers that layer.

## 8. What the first run on QNX 8.0 changed

| Decision | Why |
|---|---|
| HAM replaced by `cordon-supervise` | HAM is an optional package (`com.qnx.qnx800.target.os_services.ham`, versions 0.0.1.00135T202311191043L and 0.0.2.02005T202411230033L in the QNX 8.0 repository) but it is not in the installed baseline: no `ham` binary, no libham, no headers under `$QNX_TARGET`, and the QSC install path for it answers HTTP 401 without myQNX credentials. The restart semantics are therefore implemented with posix_spawn, waitpid and slog2, which are the primitives a HAM rule would drive. Enabling the real thing is one package install plus an entity file, and needs the myQNX password once. The scenario is named for what the image does: supervised-restart. |
| `cordon-view` instead of the screen process | The image is built with `--graphics=no`; there is no QNX Screen to kill. The scenario keeps its meaning and is named for it: the process that consumes the service is terminated. |
| Permission check needs the object type, not 0 | `secpol_check_permission(info, otype, perm)` matches rules written against `cordon_t` as the target type. Passing 0 matched nothing and denied every request. The service now resolves `cordon_t` at startup and logs the number it found. |
| Client type reported by the server, not by the client | `secpol_type_id()` is documented as accurate only for processes that change type with `secpol_transition_type()`; a process started with `on -T` reported 0. The message the service receives carries the client's type, and that is what the log shows. |
| sshd host key pre-generated into the data partition | The key is not part of what the gate tests, and the emulated target does not need to create it on every boot. The image ships a key generated once at build time. |
| Two images, not one | C5 needs an image that actually breaks the startup order; a script that pretends to fail would say nothing about startup order. |
