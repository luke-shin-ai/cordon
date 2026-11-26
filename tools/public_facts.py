#!/usr/bin/env python3
"""Cross check the public surfaces against the evidence bundle.

A public surface is any text a judge can read: the README, the design notes,
the codelab, the submission draft. Every factual token in those texts is
compared with the bundle that the run produced:

  scenario names, and a failure if a surface states the result of one
  image hashes and the source commit
  errno values and the policy id
  counts of scenarios, claims and bundle files
  file paths that the text names

The verdicts of the run are not published while the section 4.6(i) approval is
outstanding, so a sentence that states one is reported as a problem.

  python3 tools/public_facts.py --bundle ../../hackathons/qnx-2026/evidence/g2-core \
      README.md docs codelab/markdown

Exit 0 when every surface agrees with the bundle, 1 otherwise.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "host"))

import cordon_notes as notes  # noqa: E402

# The startup order check has an internal name that is not a scenario of its
# own. Its result lives inside the all-pass evidence, so a surface may use it.
INTERNAL_NAMES = {"boot-order"}
# Only tokens of these shapes are read as scenario names, so a file name such as
# cordon-boot.sh is never mistaken for one.
SCENARIO_PREFIXES = ("kill-", "deny-", "allow-", "supervised-", "boot-order", "all-pass")

PATH_PREFIXES = ("target/", "host/", "docs/", "codelab/", "tools/", "tests/")
ROOT_FILES = {"README.md", "Makefile", "LICENSE", "claims.yaml", ".gitignore"}
BACKTICK = re.compile(r"`([^`\n]+)`")
VERDICT_NEAR = re.compile(r"\b(pass(?:es|ed)?|fail(?:s|ed)?)\b", re.IGNORECASE)
WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def text_files(paths: list) -> list:
    files = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            for child in sorted(path.rglob("*.md")):
                if any(part in {".git", "__pycache__", "out", "site", "node_modules"} for part in child.parts):
                    continue
                files.append(child)
        elif path.is_file():
            files.append(path)
    return files


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("surfaces", nargs="*")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--claims", type=Path, default=ROOT / "claims.yaml")
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--devpost", type=Path, default=None)
    args = parser.parse_args(argv)

    problems = []
    checks = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        checks.append((name, condition))
        if not condition:
            problems.append(f"{name}: {detail}")

    try:
        bundle = notes.load_bundle(args.bundle, args.claims)
    except (notes.BundleError, ValueError) as exc:
        print(f"public_facts: {exc}", file=sys.stderr)
        return 2

    check("bundle manifest complete", bool(bundle.manifest.get("complete")),
          "manifest.json does not say complete")
    check("bundle file count", len(bundle.manifest.get("files", [])) == 5,
          f"manifest lists {len(bundle.manifest.get('files', []))} files")

    expected = {c["scenario"]: c for c in bundle.claims}
    check("ledger covers bundle", set(expected) == set(bundle.verdicts),
          f"ledger {sorted(expected)} against bundle {sorted(bundle.verdicts)}")
    for scenario, claim in expected.items():
        verdict = bundle.verdicts.get(scenario)
        check(f"ledger {claim['id']} verdict", verdict == claim.get("expected_result", "PASS"),
              f"{scenario} is {verdict}, ledger expects {claim.get('expected_result', 'PASS')}")
        check(f"ledger {claim['id']} status", claim.get("status") == "declared",
              f"{scenario} status is {claim.get('status')}, ledger should say declared")

    if "boot-order" in INTERNAL_NAMES:
        check("boot-order evidence", "boot-order=PASS" in bundle.text,
              "no boot-order result inside the bundle")

    surfaces = text_files(args.surfaces)
    if args.devpost is not None:
        surfaces.append(args.devpost)

    for path in surfaces:
        text = path.read_text(encoding="utf-8", errors="replace")
        label = str(path)

        for token in re.findall(r"\b[a-z0-9]+(?:-[a-z0-9]+)+\b", text):
            if token in bundle.verdicts or token in INTERNAL_NAMES:
                continue
            if token.startswith(SCENARIO_PREFIXES):
                check(f"{label} scenario name", False, f"unknown scenario {token}")

        for name in bundle.verdicts:
            for match in re.finditer(re.escape(name), text):
                window = text[match.end():match.end() + 60].split("\n")[0]
                stated = VERDICT_NEAR.search(window)
                if stated:
                    check(f"{label} result for {name}", False,
                          f"states a result ({stated.group(0)}) next to {name}; "
                          "verdicts are not published while 4.6(i) approval is outstanding")

        for phrase in ("six of six", "6 of 6", "GATE: PASS", "gate PASS",
                       "Guaranteed by a PASS", "status: verified", "verified on"):
            if phrase.lower() in text.lower():
                check(f"{label} unpublished result phrase", False, f"contains {phrase!r}")

        for cid in set(re.findall(r"\bC[1-6]\b", text)):
            claim = bundle.claim(cid)
            check(f"{label} claim {cid}", claim is not None, "claim id not in ledger")

        for h in notes.HASH64.findall(text):
            check(f"{label} sha256", h in bundle.hashes, f"{h} is not in the bundle")
        for h in notes.HASH40.findall(text):
            check(f"{label} commit", bundle.commit.startswith(h), f"{h} is not the run commit")
        for value in notes.ERRNO.findall(text):
            check(f"{label} errno", f"errno={value}" in bundle.text, f"errno={value} not observed")
        for name, value in notes.ERRNO_NAMES.items():
            if name not in ("EACCES", "EPERM", "ENOENT"):
                continue
            if re.search(rf"\b{name}\b", text):
                check(f"{label} errno name", f"errno={value}" in bundle.text, f"{name} not observed")
        for pid in notes.POLICY_ID.findall(text):
            check(f"{label} policy id", pid.lower() in bundle.text.lower() or pid[2:].lower() in bundle.text.lower(),
                  f"{pid} not reported by the run")

        for raw_count, noun in notes.COUNT.findall(text):
            value = WORD_NUMBERS.get(raw_count.lower()) or int(raw_count)
            actual = {"scenarios": len(bundle.scenarios), "scenario": len(bundle.scenarios),
                      "claims": len(bundle.claims), "claim": len(bundle.claims),
                      "files": len(bundle.manifest.get("files", [])),
                      "file": len(bundle.manifest.get("files", [])),
                      "checks": len(bundle.claims), "check": len(bundle.claims)}.get(noun.lower())
            if actual is not None:
                check(f"{label} count {noun}", value == actual, f"{raw_count} against {actual}")
        for word in ("six of six", "6 of 6", "six scenarios", "6 scenarios"):
            if word in text.lower():
                check(f"{label} ratio", len(bundle.scenarios) == 6, f"{word} against {len(bundle.scenarios)} scenarios")

        for token in BACKTICK.findall(text):
            candidate = token.strip().rstrip("/").strip()
            if not candidate or " " in candidate:
                continue
            if not (candidate.startswith(PATH_PREFIXES) or candidate in ROOT_FILES):
                continue
            check(f"{label} path {candidate}", (args.repo / candidate).exists(),
                  f"{candidate} does not exist in the repository")

    if args.devpost is not None:
        draft = args.devpost.read_text(encoding="utf-8")
        for phrase in ("QNX 8.0", "QEMU", "qcc", "resource manager", "secpol",
                       "message passing", "startup", "real-time"):
            check(f"devpost {phrase}", phrase.lower() in draft.lower(), f"missing {phrase}")

    for name, condition in checks:
        if not condition:
            continue
    print(f"public_facts: {len(checks)} checks, {len(problems)} problem(s)")
    for problem in problems:
        print(f"  FAIL {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
