#!/usr/bin/env python3
"""Codelab format tests: metadata, validation, and the static renderer.

The rules under test are the QNX codelabs metadata standard (id, categories,
tags, platforms, published, status) and the promise that the rendered page is
self contained HTML with no build step.

Run: python3 tests/test_codelab.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "codelab"))

import render  # noqa: E402

SOURCE = ROOT / "codelab" / "markdown" / "cordon-release-gate" / "cordon-release-gate.md"


def main() -> int:
    failures = []

    codelab = render.load(SOURCE)
    problems = render.validate(codelab)
    if problems:
        failures.append("the shipped codelab should validate: " + "; ".join(problems))

    for key in render.REQUIRED_KEYS:
        if not codelab.meta.get(key):
            failures.append(f"metadata {key} should be present")
    if codelab.meta.get("tags") not in render.LEVELS:
        failures.append("tags should carry exactly one level")
    if codelab.meta.get("id") != SOURCE.stem:
        failures.append("id should equal the file name")
    if len(codelab.steps) < 5:
        failures.append("the codelab should have at least five sections")
    if not any(step.duration for step in codelab.steps):
        failures.append("at least one section should carry a duration")

    if not codelab.includes:
        failures.append("the codelab should take its listings from repository files")
    for rel in codelab.includes:
        source = ROOT / rel
        if not source.is_file():
            failures.append(f"included file should exist: {rel}")
            continue
        body = source.read_text(encoding="utf-8").rstrip("\n")
        if body not in codelab.text:
            failures.append(f"the listing for {rel} should equal the file byte for byte")
        if f"file={rel}" in codelab.text:
            failures.append(f"the file= marker for {rel} should be gone after expansion")

    missing = Path(tempfile.mkdtemp(prefix="cordon-include-")) / "gone.md"
    missing.write_text(SOURCE.read_text(encoding="utf-8").replace(
        "file=target/secpol/cordon.policy", "file=target/secpol/no-such-file.policy"),
        encoding="utf-8")
    problems = render.validate(render.load(missing))
    if not any("include target not found" in problem for problem in problems):
        failures.append("a missing include target should fail validation")

    page = render.render(codelab)
    if "<html" not in page or "</html>" not in page:
        failures.append("the renderer should produce a whole document")
    if page.count("<section") != len(codelab.steps):
        failures.append("every section should be rendered")
    if "&lt;stdio.h&gt;" not in page:
        failures.append("code blocks should be escaped")
    if "cordon.policy" not in page:
        failures.append("the policy step should be in the page")

    broken_root = Path(tempfile.mkdtemp(prefix="cordon-codelab-")) / "markdown"
    broken_dir = broken_root / "wrong-name"
    broken_dir.mkdir(parents=True)
    broken = broken_dir / "wrong-name.md"
    text = SOURCE.read_text(encoding="utf-8")
    text = text.replace("tags: intermediate", "tags: expert")
    text = text.replace("summary:", "summary-broken:")
    broken.write_text(text, encoding="utf-8")
    problems = render.validate(render.load(broken))
    codes = " ".join(problems)
    if "tags must be one of" not in codes:
        failures.append("an unknown level should fail validation")
    if "missing metadata key: summary" not in codes:
        failures.append("a missing key should fail validation")

    out = Path(tempfile.mkdtemp(prefix="cordon-site-")) / "site"
    if render.main(["--root", str(SOURCE.parent.parent), "--out", str(out)]) != 0:
        failures.append("rendering the shipped codelab should succeed")
    if not (out / "index.html").exists() or not (out / "cordon-release-gate.html").exists():
        failures.append("the renderer should write index and one page")
    shutil.rmtree(out.parent, ignore_errors=True)

    if failures:
        for f in failures:
            print("FAIL:", f)
        return 1
    print("PASS: 11 codelab cases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
