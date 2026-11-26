#!/usr/bin/env python3
"""Gate rule tests. Synthetic bundles only, no QNX target needed.

Run: python3 tests/test_gate.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORDON = ROOT / "host" / "cordon"

POSITIVE = ["kill-screen", "deny-unprivileged-write", "allow-privileged-write", "ham-restart", "all-pass"]
NEGATIVE = "boot-order-violation"


def expected_rows() -> list[dict]:
    rows = [{"name": n, "verdict": "PASS"} for n in POSITIVE]
    rows.append({"name": NEGATIVE, "verdict": "FAIL"})
    return rows


def bundle(rows: list[dict]) -> Path:
    d = Path(tempfile.mkdtemp(prefix="cordon-test-"))
    (d / "scenarios.json").write_text(json.dumps({"scenarios": rows}), encoding="utf-8")
    return d


def gate(path: Path) -> int:
    return subprocess.run([sys.executable, str(CORDON), "gate", str(path)], capture_output=True).returncode


def main() -> int:
    failures = []

    if gate(bundle(expected_rows())) != 0:
        failures.append("every scenario as expected should pass")

    bad = expected_rows()
    for row in bad:
        if row["name"] == NEGATIVE:
            row["verdict"] = "PASS"
    if gate(bundle(bad)) != 1:
        failures.append("negative scenario that passed should fail the gate")

    if gate(bundle(expected_rows()[:-1])) != 1:
        failures.append("missing scenario should fail the gate")

    if failures:
        for f in failures:
            print("FAIL:", f)
        return 1
    print("PASS: 3 gate cases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
