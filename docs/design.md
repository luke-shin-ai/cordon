# Cordon design notes

Written 2026-11-26. This file fixes what "pass" means before any code is
written, so the scenarios cannot drift toward whatever the implementation
happens to do.

## 1. Scenario contract

Six scenarios. Each one is a shell-visible action plus one observed fact. A
scenario has a name, a precondition, an action, an expected observation, and a
verdict field that is exactly `PASS` or `FAIL`. There is no partial credit and
no score.

| Scenario | Precondition | Action | PASS when |
|---|---|---|---|
| kill-screen | normal image booted, guarded service running | terminate the screen process | guarded service still answers its status query |
| deny-unprivileged-write | normal image booted | unprivileged client writes to /dev/cordon | write is refused and the guarded service stays up |
| allow-privileged-write | normal image booted | permitted client writes to /dev/cordon | write succeeds and one record appears in the event log |
| ham-restart | normal image booted | terminate the guarded process | process is running again and the restart is in the event log |
| boot-order-violation | image built with the wrong startup order | boot | gate returns FAIL and names the violated order |
| all-pass | normal image booted | run all five above | five PASS and gate returns PASS |

## 2. What the evidence bundle contains

`cordon bundle` writes one directory per run:

| File | Content |
|---|---|
| `scenarios.json` | per scenario: name, verdict, start and end time, exit code |
| `console.log` | serial console text from the target for the run |
| `slog2.txt` | slog2 dump from the target |
| `image.json` | buildfile hash, image hash, QNX version string |
| `sources.json` | git commit of the repository and the toolchain version string |

The bundle is the only input the gate and the AI layer may read.

## 3. Gate rule

`cordon gate` exits 0 only when every non-negative scenario is PASS and every
negative scenario is FAIL. Any other combination exits 1 and prints the failed
scenario names. The exit code is the product: a build system can use it without
parsing text.

## 4. AI layer rule

The AI layer drafts a release note from `scenarios.json` only. A deterministic
checker then deletes any sentence whose claim cannot be matched to a scenario
record (process name, file path, or verdict). Sentences that survive keep the
scenario id. This is the only place a model is used, and it cannot invent a
pass.

## 5. Guaranteed and not guaranteed

Guaranteed by a PASS bundle: the six scenarios above were executed on the image
whose hashes are in `image.json`, and their observations were as stated.

Not guaranteed: that the image is correct outside these six scenarios, that it
meets any safety certification, or anything about latency, throughput or
resource usage. The license for the QNX non-commercial toolchain restricts
publishing the results of performance or functional evaluation (section 4.6(i)),
so no such numbers appear in this repository, in the submission text or in the
video.

## 6. Open questions to settle at G2

1. Whether `/dev/cordon` should be a single-threaded resource manager with
   `resmgr_attach` plus a dispatch loop, or a message-passing pulse handler only.
2. Whether the startup order check belongs in the gate or in the image build.
3. Whether HAM restart recording needs its own resource manager path or can go
   straight into slog2 and be read back by the host.
