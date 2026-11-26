#!/usr/bin/env python3
"""Judging rule tests. Synthetic console text only, no QNX target needed.

The rules under test are the ones docs/design.md states: the target prints
CORDON-OBS lines and host/cordon decides. These tests feed it text and check
that a missing or wrong observation changes the verdict.

Run: python3 tests/test_run.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load() -> object:
    loader = importlib.machinery.SourceFileLoader("cordon_cli", str(ROOT / "host" / "cordon"))
    spec = importlib.util.spec_from_loader("cordon_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


CORDON = load()

NORMAL = """\
CORDON-G2-START mode=normal
CORDON-OBS policy push=OK
CORDON-OBS supervise start kind=up pid=100 service=/proc/boot/cordon max_restarts=4 type=cordon_t
CORDON-OBS supervise spawn kind=spawn seq=1 pid=101
CORDON-OBS service=up kind=start seq=1 pid=101 path=/dev/cordon zone=zone-main policy=0x1 object_type=1 write_perm=present status_perm=present
CORDON-OBS service path=/dev/cordon state=present tries=1
CORDON-OBS client role=order-probe type=cordon_view_t op=status result=OK status=pid=101 seq=1 zone=zone-main events=0 policy=0x1 last=none
CORDON-OBS service=deny kind=write pid=200 type=cordon_probe_t result=DENY check=EPERM
CORDON-OBS client role=probe type=cordon_probe_t op=write result=ERR errno=13(Permission denied)
CORDON-OBS client role=outsider type=cordon_outsider_t op=open result=ERR errno=1(Operation not permitted)
CORDON-OBS client role=deny-after type=cordon_view_t op=status result=OK status=pid=101 seq=1 zone=zone-main events=0 policy=0x1 last=none
CORDON-OBS service=accept kind=write pid=201 type=cordon_writer_t result=ACCEPT bytes=22 record=zone=main state=sealed
CORDON-OBS client role=writer type=cordon_writer_t op=write result=OK bytes=22
CORDON-OBS client role=writer-after type=cordon_view_t op=status result=OK status=pid=101 seq=1 zone=zone-main events=1 policy=0x1 last=zone=main state=sealed
CORDON-OBS view role=view type=cordon_view_t state=up pid=300 path=/dev/cordon ticks=60
CORDON-OBS kill target=view pid=300 result=OK
CORDON-OBS view pid=300 state=gone
CORDON-OBS client role=view-after type=cordon_view_t op=status result=OK status=pid=101 seq=1 zone=zone-main events=1 policy=0x1 last=zone=main state=sealed
CORDON-OBS client role=pre-kill type=cordon_view_t op=status result=OK status=pid=101 seq=1 zone=zone-main events=1 policy=0x1 last=zone=main state=sealed
CORDON-OBS kill target=service pid=101 result=OK
CORDON-OBS supervise exit kind=exit seq=1 pid=101 signal=9 code=-1
CORDON-OBS supervise respawn kind=respawn next_seq=2
CORDON-OBS supervise spawn kind=spawn seq=2 pid=102
CORDON-OBS service=up kind=start seq=2 pid=102 path=/dev/cordon zone=zone-main policy=0x1 object_type=1 write_perm=present status_perm=present
CORDON-OBS client role=post-kill type=cordon_view_t op=status result=OK status=pid=102 seq=2 zone=zone-main events=0 policy=0x1 last=none
CORDON-OBS client role=restart-after type=cordon_view_t op=status result=OK status=pid=102 seq=2 zone=zone-main events=0 policy=0x1 last=none
CORDON-G2-COMPLETE mode=normal
"""

VIOLATED = """\
CORDON-G2-START mode=violated
CORDON-OBS policy push=OK
CORDON-OBS order check=first-client-before-service attempting=yes
CORDON-OBS client role=order-probe type=cordon_view_t op=status result=ERR errno=2(No such file or directory)
CORDON-OBS service=up kind=start seq=1 pid=101 path=/dev/cordon zone=zone-main policy=0x1 object_type=1 write_perm=present status_perm=present
CORDON-OBS client role=order-after-start type=cordon_view_t op=status result=OK status=pid=101 seq=1 zone=zone-main events=0 policy=0x1 last=none
CORDON-G2-COMPLETE mode=violated
"""


def judge(text: str) -> dict:
    CORDON._CURRENT = CORDON.parse_observations(text)
    return CORDON.judge_normal(CORDON._CURRENT)


def judge_neg(text: str) -> dict:
    CORDON._CURRENT = CORDON.parse_observations(text)
    return CORDON.judge_negative(CORDON._CURRENT)


def main() -> int:
    failures = []

    rows = judge(NORMAL)
    for name in ("boot-order", "kill-consumer", "deny-unprivileged-write",
                 "allow-privileged-write", "supervised-restart", "all-pass"):
        if rows[name]["verdict"] != "PASS":
            failures.append(f"{name} should pass on the full normal console")

    # A write that was accepted for the wrong type must fail the deny scenario.
    broken = NORMAL.replace(
        "client role=probe type=cordon_probe_t op=write result=ERR errno=13(Permission denied)",
        "client role=probe type=cordon_probe_t op=write result=OK bytes=22")
    if judge(broken)["deny-unprivileged-write"]["verdict"] != "FAIL":
        failures.append("an accepted unprivileged write should fail deny-unprivileged-write")

    # No respawn means no restart evidence, whatever the kill says.
    broken = NORMAL.replace("CORDON-OBS supervise respawn kind=respawn next_seq=2\n", "")
    broken = broken.replace(
        "client role=restart-after type=cordon_view_t op=status result=OK status=pid=102 seq=2",
        "client role=restart-after type=cordon_view_t op=status result=OK status=pid=101 seq=1")
    if judge(broken)["supervised-restart"]["verdict"] != "FAIL":
        failures.append("missing restart evidence should fail supervised-restart")

    # The consumer that is killed must actually be gone.
    broken = NORMAL.replace("view pid=300 state=gone", "view pid=300 state=alive")
    if judge(broken)["kill-consumer"]["verdict"] != "FAIL":
        failures.append("a surviving consumer should fail kill-consumer")

    # A correctly ordered image makes the negative row PASS, which fails the gate.
    ok_image = VIOLATED.replace(
        "client role=order-probe type=cordon_view_t op=status result=ERR errno=2(No such file or directory)",
        "client role=order-probe type=cordon_view_t op=status result=OK status=pid=101 seq=1")
    if judge_neg(ok_image)["boot-order-violation"]["verdict"] != "PASS":
        failures.append("an image that answers before the first client should not earn FAIL")

    # The misordered image earns FAIL.
    if judge_neg(VIOLATED)["boot-order-violation"]["verdict"] != "FAIL":
        failures.append("the misordered image should earn FAIL")

    if failures:
        for f in failures:
            print("FAIL:", f)
        return 1
    print(f"PASS: 6 run-rule cases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
