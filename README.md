# Cordon

A release decision gate for QNX images. Cordon runs a fixed set of verification
scenarios against a QNX 8.0 target and answers one question: does this image
pass, or does it not.

Status: skeleton. No QNX build has been run yet, because the SDP 8.0 install is
blocked on the myQNX account credential in the build environment. Every file
under `target/` is unbuilt and unverified. What exists and is real today is the
claim ledger (`claims.yaml`), the scenario contract (`docs/design.md`) and the
host CLI skeleton.

## What it does

Cordon is not a tracing tool and not a profiler. It does not publish latency or
throughput numbers. It records whether each of six scenarios passed or failed,
with the raw console evidence kept in a bundle, and refuses to produce a release
note that claims more than the evidence supports.

## Layout

| Path | Contents |
|---|---|
| `target/resmgr/cordon.c` | resource manager that owns `/dev/cordon` (QNX 8.0 x86_64) |
| `target/secpol/cordon.policy` | security policy: only the allowlisted process may write to `/dev/cordon` |
| `target/ham/cordon.ham` | HAM rule: restart the guarded service and record the event |
| `target/boot/buildfile` | startup order, plus the deliberately wrong image used as a negative case |
| `host/cordon` | host CLI: `claims`, `run`, `gate`, `bundle` |
| `claims.yaml` | the six claims, each with its scenario, evidence source and status |
| `docs/design.md` | scenario contract, PASS/FAIL rules, guaranteed and not guaranteed |

## Scope line

Fixed on purpose, so it stays finishable: one resource manager, six scenarios,
one security policy file, one HAM rule, four CLI commands, one codelab. Nothing
beyond this line is added before the hackathon submission.

## License

Apache License 2.0, see `LICENSE`.
