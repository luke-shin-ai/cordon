#!/usr/bin/env python3
"""Scan text files for credentials and credential shaped strings.

The myQNX password used once during setup is the specific worry: it must not
appear in any log, document, script or commit. The same scan covers the usual
shapes of cloud keys, tokens and private keys so a later copy and paste cannot
slip one in.

  python3 tools/secret_scan.py README.md docs host target tests tools
  python3 tools/secret_scan.py --ignore license-key ../../hackathons

Exit 0 when nothing is found, 1 when at least one hit is reported. A line that
has to name a field, such as a usage message, can carry `secret-scan: allow`.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SKIP_DIRS = {".git", "__pycache__", "out", "build", "site", "node_modules", ".venv"}
TEXT_SUFFIXES = {".md", ".py", ".c", ".h", ".sh", ".txt", ".html", ".css", ".js",
                 ".yaml", ".yml", ".json", ".policy", ".build", ".buildfile",
                 ".log", ".ini", ".conf", ".env", ""}

# scan-exempt-begin
PATTERNS = (
    ("password", re.compile(r"(?:^|[\s\"'`/;-])(?:-myqnx\.)?password\s*[=:]\s*(\S+)", re.IGNORECASE)),
    ("token", re.compile(r"\b(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
                         r"xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{30,}|"
                         r"sk-[A-Za-z0-9]{20,}|nvapi-[A-Za-z0-9_-]{20,})")),
    ("cloud-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("license-key", re.compile(r"\b[A-Z0-9]{4}(?:-[A-Z0-9]{4}){4}\b")),
)
# scan-exempt-end

ALLOW = "secret-scan: allow"
SAFE_VALUES = ("$", "*", "<", "%", "{", "(", "[", "\\", "password", "pass", "none", "null")


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


def scan(path: Path, ignored: set) -> list:
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
        for name, pattern in PATTERNS:
            if name in ignored:
                continue
            match = pattern.search(line)
            if not match:
                continue
            if name == "password":
                value = match.group(1).strip("\"'`;,")
                if value.startswith(SAFE_VALUES) or len(value) < 4:
                    continue
            hits.append((path, lineno, name))
            break
    return hits


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--ignore", default="", help="comma separated classes to skip")
    args = parser.parse_args(argv)
    ignored = {name.strip() for name in args.ignore.split(",") if name.strip()}

    hits = []
    files = text_files(args.paths)
    for path in files:
        hits.extend(scan(path, ignored))
    for path, lineno, name in hits:
        print(f"{path}:{lineno}: {name}")
    print(f"secret_scan: {len(hits)} hit(s) in {len(files)} file(s), ignored={sorted(ignored)}")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
