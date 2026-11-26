#!/usr/bin/env python3
"""Tests for the release note drafter and the deterministic claim verifier.

No QNX target and no network: the bundle below is synthetic, and the two
providers that matter for the test are the template drafter and the verifier.
The optional proxy provider is not exercised, because a test must give the same
answer offline.

Run: python3 tests/test_note.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "host"))

import cordon_notes as notes  # noqa: E402

IFS = "a" * 64
NEG = "b" * 64
COMMIT = "c" * 40

SCENARIOS = {
    "scenarios": [
        {"name": "kill-consumer", "verdict": "PASS", "rule": "the consumer is terminated",
         "evidence": ["kill target=view pid=300 result=OK", "view pid=300 state=gone"]},
        {"name": "deny-unprivileged-write", "verdict": "PASS", "rule": "the write is refused",
         "evidence": ["client role=probe op=write result=ERR errno=13(Permission denied)",
                      "client role=outsider op=open result=ERR errno=1(Operation not permitted)"]},
        {"name": "allow-privileged-write", "verdict": "PASS", "rule": "the writer is accepted",
         "evidence": ["client role=writer op=write result=OK bytes=22"]},
        {"name": "supervised-restart", "verdict": "PASS", "rule": "the service is started again",
         "evidence": ["supervise respawn kind=respawn next_seq=2"]},
        {"name": "boot-order-violation", "verdict": "FAIL", "rule": "the image earned FAIL",
         "evidence": ["client role=order-probe op=status result=ERR errno=2(No such file)"]},
        {"name": "all-pass", "verdict": "PASS", "rule": "all five pass",
         "evidence": ["kill-consumer=PASS", "supervised-restart=PASS"]},
    ]
}

IMAGE = {
    "qnx_version": "QNX test 8.0.0 2026/10/30 x86pc x86_64",
    "normal": {"booted": {"normal-ifs.bin": {"sha256": IFS}}},
    "negative": {"booted": {"violated-ifs.bin": {"sha256": NEG}}},
}

SOURCES = {"repo_commit": COMMIT, "repo_dirty": False, "qcc": ["12.2.0,gcc_ntox86_64"]}

CONSOLE = """\
QNX test 8.0.0 2026/10/30-09:10:05EDT x86pc x86_64
CORDON-OBS policy id=policy id:995f85da (immutable)
CORDON-OBS client role=probe op=write result=ERR errno=13(Permission denied)
CORDON-OBS client role=order-probe op=status result=ERR errno=2(No such file or directory)
CORDON-G2-COMPLETE mode=normal
"""

SLOG = "cordon respawn kind=respawn next_seq=2\n"


def make_bundle(root: Path) -> Path:
    d = root / "bundle"
    d.mkdir()
    (d / "scenarios.json").write_text(json.dumps(SCENARIOS), encoding="utf-8")
    (d / "image.json").write_text(json.dumps(IMAGE), encoding="utf-8")
    (d / "sources.json").write_text(json.dumps(SOURCES), encoding="utf-8")
    (d / "console.log").write_text(CONSOLE, encoding="utf-8")
    (d / "slog2.txt").write_text(SLOG, encoding="utf-8")
    (d / "manifest.json").write_text(json.dumps(
        {"files": ["scenarios.json", "console.log", "slog2.txt", "image.json", "sources.json"],
         "complete": True}), encoding="utf-8")
    return d


def main() -> int:
    failures = []
    tmp = Path(tempfile.mkdtemp(prefix="cordon-note-"))
    bundle = notes.load_bundle(make_bundle(tmp), ROOT / "claims.yaml")

    text = notes.draft(bundle)
    report = notes.verify(text, bundle)
    if report["outcome"] != "VERIFIED":
        failures.append("the template draft should verify")
    if report["dropped"] or report["rejected"]:
        failures.append("the template draft should have no dropped sentence")
    if notes.draft(bundle) != text:
        failures.append("the template drafter should be deterministic")
    if notes.rebuild(text, report).count("[C") < 6:
        failures.append("the rebuilt note should keep the claim ids")

    bad = text + "\nAll six scenarios passed, including the misordered image. [C5]\n"
    bad_report = notes.verify(bad, bundle)
    dropped_texts = [row["text"] for row in bad_report["units"] if not row["kept"]]
    if not any("misordered" in t for t in dropped_texts):
        failures.append("a sentence that contradicts the misordered image should be dropped")
    if bad_report["outcome"] != "VERIFIED":
        failures.append("dropping a false sentence should still leave a verified note")

    bad = text + f"\nThe image is {NEG.replace('b', 'd')}. [image]\n"
    if not any("unverified-hash" in [d["code"] for d in row["defects"]]
               for row in notes.verify(bad, bundle)["units"]):
        failures.append("an unknown image hash should be dropped")

    bad = text + "\nThe run was built from commit " + ("d" * 40) + ". [sources]\n"
    if not any("unverified-commit" in [d["code"] for d in row["defects"]]
               for row in notes.verify(bad, bundle)["units"]):
        failures.append("an unknown commit should be dropped")

    perf = text + "\nThe service answered in 0.4 ms. [C1]\n"  # number-scan: allow
    perf_report = notes.verify(perf, bundle)
    if perf_report["outcome"] != "REJECTED":
        failures.append("a performance claim should reject the whole note")

    pct = text + "\nThe gate passed 99% of runs. [gate]\n"  # number-scan: allow
    if notes.verify(pct, bundle)["outcome"] != "REJECTED":
        failures.append("a share of a whole should reject the whole note")

    style = text + "\nThe gate returns PASS \u2014 for this bundle. [gate]\n"
    if notes.verify(style, bundle)["outcome"] != "REJECTED":
        failures.append("a forbidden character should reject the whole note")

    unsupported = notes.verify("# Note\n\nThe system is production ready.\n", bundle)
    if unsupported["outcome"] != "REJECTED":
        failures.append("a note with only unsupported sentences should be rejected")

    missing = tmp / "empty"
    missing.mkdir()
    try:
        notes.load_bundle(missing, ROOT / "claims.yaml")
        failures.append("a bundle without files should raise BundleError")
    except notes.BundleError:
        pass

    if failures:
        for f in failures:
            print("FAIL:", f)
        return 1
    print("PASS: 10 note cases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
