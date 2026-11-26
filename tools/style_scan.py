#!/usr/bin/env python3
"""Scan text files for characters and phrases that mark machine written copy.

The rule for this repository: public text uses ASCII punctuation only. No em
dash, en dash, arrows, middle dots, decorative bullets, smart quotes, ellipsis
characters or emoji, and none of the stock phrases that make a text read as
generated.

  python3 tools/style_scan.py README.md docs codelab host target tests

Exit 0 when nothing is found, 1 when at least one hit is reported. Lines that
are deliberately excluded can be marked with `style-scan: allow` in the same
line, so a rule that has to quote a forbidden character can say so.
"""

from __future__ import annotations

import sys
from pathlib import Path

SKIP_DIRS = {".git", "__pycache__", "out", "build", "site", "node_modules", ".venv"}
TEXT_SUFFIXES = {".md", ".py", ".c", ".h", ".sh", ".txt", ".html", ".css", ".js",
                 ".yaml", ".yml", ".json", ".policy", ".build", ".buildfile", ""}

CHARS = {
    "\u2014": "em dash",
    "\u2013": "en dash",
    "\u2012": "figure dash",
    "\u2192": "right arrow",
    "\u2190": "left arrow",
    "\u2191": "up arrow",
    "\u2193": "down arrow",
    "\u00b7": "middle dot",
    "\u2022": "bullet character",
    "\u2026": "ellipsis character",
    "\u201c": "smart double quote",
    "\u201d": "smart double quote",
    "\u2018": "smart single quote",
    "\u2019": "smart single quote",
    "\u00a0": "non breaking space",
}

# scan-exempt-begin
PHRASES = (
    "not just",
    "game changer",
    "cutting edge",
    "seamless",
    "unlock the power",
    "dive into",
    "in today's fast paced world",
)
# scan-exempt-end

ALLOW = "style-scan: allow"


def is_emoji(ch: str) -> bool:
    value = ord(ch)
    return (0x1F300 <= value <= 0x1FAFF or 0x2600 <= value <= 0x27BF
            or value in (0xFE0F, 0x1F1E6) or 0x1F000 <= value <= 0x1F0FF)


def text_files(paths: list) -> list:
    files = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if any(part in SKIP_DIRS for part in child.relative_to(path).parts):
                    continue
                if child.is_file() and child.suffix in TEXT_SUFFIXES:
                    files.append(child)
        elif path.is_file():
            files.append(path)
    return files


def scan(path: Path) -> list:
    hits: list = []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return hits
    if "\x00" in text:
        return hits
    exempt = False
    for lineno, line in enumerate(text.splitlines(), 1):
        if "scan-exempt-begin" in line:
            exempt = True
            continue
        if "scan-exempt-end" in line:
            exempt = False
            continue
        if exempt or ALLOW in line:
            continue
        for ch, name in CHARS.items():
            column = line.find(ch)
            if column >= 0:
                hits.append((path, lineno, column + 1, name))
                break
        if any(is_emoji(ch) for ch in line):
            hits.append((path, lineno, 1, "emoji"))
        lowered = line.lower()
        for phrase in PHRASES:
            if phrase in lowered:
                hits.append((path, lineno, lowered.index(phrase) + 1, f"phrase: {phrase}"))
                break
    return hits


def main(argv: list) -> int:
    if not argv:
        print("usage: style_scan.py PATH [PATH ...]", file=sys.stderr)
        return 2
    hits = []
    files = text_files(argv)
    for path in files:
        hits.extend(scan(path))
    for path, lineno, column, name in hits:
        print(f"{path}:{lineno}:{column}: {name}")
    print(f"style_scan: {len(hits)} hit(s) in {len(files)} file(s)")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
