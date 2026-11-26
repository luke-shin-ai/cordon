#!/usr/bin/env python3
"""Scan public text for performance measurement numbers.

The non-commercial QNX license restricts publishing the results of a
performance or functional evaluation without written approval, so this
repository publishes verdicts, identifiers and hashes only. This scan enforces
that: any stated duration, rate, share of a whole, or comparison is a hit.

  python3 tools/number_scan.py README.md docs codelab host target

Exit 0 when nothing is found, 1 otherwise. A line that must discuss the rule
itself can carry `number-scan: allow`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SKIP_DIRS = {".git", "__pycache__", "out", "build", "site", "node_modules", ".venv"}
TEXT_SUFFIXES = {".md", ".py", ".c", ".h", ".sh", ".txt", ".html", ".css", ".js",
                 ".yaml", ".yml", ".json", ".policy", ".build", ".buildfile", ""}

# scan-exempt-begin
PERF_WORDS = (
    "latency", "throughput", "benchmark", "jitter", "frames per second",
    "per second", "ops/sec", "ops per second", "req/s", "requests per second",
    "mb/s", "gb/s", "speedup", "speed-up", "faster than", "slower than",
    "boot time", "startup time", "response time", "cpu usage", "memory usage",
    "resource usage", "bandwidth", "percentil", "percent", "millisecond",
    "microsecond", "nanosecond", "elapsed time", "wall clock",
)
# scan-exempt-end

# scan-exempt-begin
NUMBER_WITH_UNIT = re.compile(
    r"\b\d[\d,]*(?:\.\d+)?\s*(?:ms|msec|millisecond|milliseconds|us|usec|microsecond|microseconds|"
    r"ns|nsec|nanosecond|nanoseconds|sec|secs|second|seconds|minutes|"
    r"fps|hz|khz|mhz|kb/s|mb/s|gb/s|kbps|mbps)\b",
    re.IGNORECASE,
)
RATE = re.compile(r"\b\d[\d,]*(?:\.\d+)?\s*(?:%|percent|per cent)\b", re.IGNORECASE)
RATIO = re.compile(r"\b\d+(?:\.\d+)?\s*x\s+(?:faster|slower|speedup)", re.IGNORECASE)
# scan-exempt-end

ALLOW = "number-scan: allow"


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
        if "ldflags" in line.lower() or "cflags" in line.lower():
            continue
        lowered = line.lower()
        for word in PERF_WORDS:
            if word in lowered:
                hits.append((path, lineno, f"word: {word}"))
                break
        for pattern, label in ((NUMBER_WITH_UNIT, "number with unit"), (RATE, "rate"), (RATIO, "comparison")):
            match = pattern.search(line)
            if match:
                hits.append((path, lineno, f"{label}: {match.group(0).strip()}"))
    return hits


def main(argv: list) -> int:
    if not argv:
        print("usage: number_scan.py PATH [PATH ...]", file=sys.stderr)
        return 2
    hits = []
    files = text_files(argv)
    for path in files:
        hits.extend(scan(path))
    for path, lineno, name in hits:
        print(f"{path}:{lineno}: {name}")
    print(f"number_scan: {len(hits)} hit(s) in {len(files)} file(s)")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
