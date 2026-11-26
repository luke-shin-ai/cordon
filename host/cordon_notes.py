"""Release note drafting and claim verification for Cordon evidence bundles.

Two halves, deliberately independent of each other:

  draft(bundle)          builds one sentence per fact from the bundle alone. It
                         is a template engine, not a model, so the same bundle
                         always produces the same bytes.
  verify(text, bundle)   checks every sentence of a draft against the same
                         bundle. It does not care where the draft came from: a
                         language model, a person, or a hand-edited file is
                         held to the same rule.

The rule: a sentence survives only when every checkable claim in it is present
in the bundle and nothing in it contradicts the bundle. A sentence with no
checkable claim at all is deleted, because the note is not allowed to say
things the evidence cannot support. A sentence that states a performance or
functional evaluation result (a duration, a rate, a share of a whole)
rejects the whole note, because the license for this toolchain forbids
publishing such results. See docs/design.md section 5.

Nothing here measures anything. Every number that reaches the note is one the
bundle already contains: a hash, a version string, an errno, or a count.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED_FILES = ("scenarios.json", "console.log", "slog2.txt", "image.json", "sources.json")
OPTIONAL_FILES = ("manifest.json",)

ERRNO_NAMES = {"EACCES": 13, "EPERM": 1, "ENOENT": 2, "EINVAL": 22, "EFAULT": 14}

FORBIDDEN_CHARS = {
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
FORBIDDEN_PHRASES = (
    "not just",
    "game changer",
    "cutting edge",
    "seamless",
    "unlock the power",
)

# A sentence that contains one of these states a performance or functional
# evaluation result. Numbers with these units are covered by NUMBER_WITH_UNIT.
PERF_WORDS = (
    "latency",
    "throughput",
    "benchmark",
    "jitter",
    "frames per second",
    "per second",
    "ops/sec",
    "ops per second",
    "req/s",
    "requests per second",
    "mb/s",
    "gb/s",
    "speedup",
    "speed-up",
    "faster",
    "slower",
    "boot time",
    "startup time",
    "response time",
    "cpu usage",
    "memory usage",
    "resource usage",
    "bandwidth",
    "percent",
)
# scan-exempt-end
# scan-exempt-begin
NUMBER_WITH_UNIT = re.compile(
    r"\b\d[\d,]*(?:\.\d+)?\s*(?:ms|msec|millisecond|milliseconds|us|usec|microsecond|microseconds|"
    r"ns|nsec|nanosecond|nanoseconds|sec|secs|second|seconds|minutes|fps|hz|khz|mhz|kb|mb|gb)\b",
    re.IGNORECASE,
)
PERCENT = re.compile(r"\b\d[\d,]*(?:\.\d+)?\s*%")
# scan-exempt-end
HASH64 = re.compile(r"\b[0-9a-f]{64}\b")
HASH40 = re.compile(r"\b[0-9a-f]{40}\b")
POLICY_ID = re.compile(r"\b0x[0-9a-f]{8}\b")
VERSION = re.compile(r"\b\d+\.\d+(?:\.\d+)+\b")
ERRNO = re.compile(r"\berrno=(\d+)\b")
VERDICT = re.compile(r"\b(pass|passes|passed|passing|fail|fails|failed|failing)\b", re.IGNORECASE)
ANCHOR = re.compile(r"\[(C[1-6]|gate|image|sources|bundle)\]")
COUNT = re.compile(r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+(scenarios?|files?|claims?|checks?|images?)\b", re.IGNORECASE)
RATIO = re.compile(r"\b(\d+)\s*/\s*(\d+)\b")
NUMBER = re.compile(r"\b\d[\d,]*(?:\.\d+)?\b")

WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

# Words that make a kebab-case token look like a scenario name. A token of this
# shape that is not in the bundle is reported, because a note that names a
# scenario the run did not produce is claiming evidence that does not exist.
SCENARIO_PREFIXES = ("kill-", "deny-", "allow-", "ham-", "boot-order", "all-pass")

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9`(])")


class BundleError(Exception):
    """The bundle is missing a file or a value the note needs."""


@dataclass
class Bundle:
    path: Path
    scenarios: list
    image: dict
    sources: dict
    manifest: dict
    console: str
    slog2: str
    claims: list
    notes: list = field(default_factory=list)

    @property
    def verdicts(self) -> dict:
        return {row["name"]: row["verdict"] for row in self.scenarios}

    @property
    def text(self) -> str:
        return "\n".join([self.console, self.slog2,
                          json.dumps(self.scenarios, sort_keys=True),
                          json.dumps(self.image, sort_keys=True),
                          json.dumps(self.sources, sort_keys=True)])

    @property
    def gate(self) -> str:
        expected = {c["scenario"]: c for c in self.claims}
        for name, verdict in self.verdicts.items():
            claim = expected.get(name)
            if claim is None:
                return "FAIL"
            if verdict != claim.get("expected_result", "PASS"):
                return "FAIL"
        return "PASS"

    @property
    def hashes(self) -> set:
        found = set()
        for blob in (json.dumps(self.image, sort_keys=True), self.console, self.slog2):
            found.update(HASH64.findall(blob))
        return found

    @property
    def commit(self) -> str:
        return str(self.sources.get("repo_commit", ""))

    def claim(self, cid: str):
        for c in self.claims:
            if c["id"] == cid:
                return c
        return None

    def evidence(self, scenario: str) -> list:
        for row in self.scenarios:
            if row["name"] == scenario:
                return list(row.get("evidence", []))
        return []


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def load_bundle(path: Path, claims_path: Path) -> Bundle:
    import yaml

    path = Path(path)
    if not path.is_dir():
        raise BundleError(f"not a directory: {path}")
    missing = [name for name in REQUIRED_FILES if not (path / name).exists()]
    if missing:
        raise BundleError("bundle is missing " + ", ".join(missing))
    scenarios_doc = json.loads(_read(path / "scenarios.json"))
    rows = scenarios_doc.get("scenarios")
    if not isinstance(rows, list) or not rows:
        raise BundleError("scenarios.json has no scenarios list")
    manifest = {}
    if (path / "manifest.json").exists():
        manifest = json.loads(_read(path / "manifest.json"))
    claims = yaml.safe_load(_read(Path(claims_path)))["claims"]
    return Bundle(
        path=path,
        scenarios=rows,
        image=json.loads(_read(path / "image.json")),
        sources=json.loads(_read(path / "sources.json")),
        manifest=manifest,
        console=_read(path / "console.log"),
        slog2=_read(path / "slog2.txt"),
        claims=claims,
    )


def bundle_digest(bundle: Bundle) -> str:
    """sha256 over the bundle file names and their contents, in name order."""
    h = hashlib.sha256()
    for name in sorted(REQUIRED_FILES + OPTIONAL_FILES):
        p = bundle.path / name
        h.update(name.encode())
        if p.exists():
            h.update(p.read_bytes())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# drafting
# ---------------------------------------------------------------------------

def _policy_ids(bundle: Bundle) -> list:
    """The policy ids the run itself reported, as 0x prefixed hex.

    secpol -Q prints a line that reads `policy id:995f85da (immutable)`, and the
    status read prints `policy=0x995f85da`. Only the first form is taken, so a
    random hex value elsewhere in a boot log cannot pass as a policy id.
    """
    found = []
    for match in re.finditer(r"policy id[=: ]+(?:policy id:)?([0-9a-f]{8})\b",
                             bundle.console + bundle.slog2):
        found.append("0x" + match.group(1))
    return found


def _image_hashes(record: dict) -> dict:
    """All name to sha256 pairs in an image record, at any depth."""
    found = {}

    def walk(node, prefix=""):
        if not isinstance(node, dict):
            return
        if isinstance(node.get("sha256"), str):
            found[prefix or "image"] = node["sha256"]
            return
        for key, value in node.items():
            walk(value, f"{prefix}{key}")

    walk(record)
    return found


def _first_hash(record: dict) -> str:
    """The sha256 of the IFS in an image record, whatever shape it has."""
    hashes = _image_hashes(record)
    for name, value in hashes.items():
        if "ifs" in name:
            return value
    for value in hashes.values():
        return value
    return ""


def draft(bundle: Bundle) -> str:
    """Build the note from the bundle. Deterministic: no clock, no randomness."""
    lines = ["# Cordon release note", ""]
    lines.append("## Decision")
    lines.append("")
    lines.append(f"The gate returns {bundle.gate} for this bundle. [gate]")

    hashes = bundle.hashes
    normal = _first_hash(bundle.image.get("normal", {}))
    negative = _first_hash(bundle.image.get("negative", {}))
    if normal:
        lines.append(f"The normal image is the IFS with sha256 {normal}. [image]")
    if negative:
        lines.append(f"The deliberately misordered image is the IFS with sha256 {negative}. [image]")
    if "8.0.0" in bundle.text:
        lines.append("The target identifies itself as QNX 8.0.0. [image]")
    lines.append("")

    lines.append("## Scenarios")
    lines.append("")
    for claim in bundle.claims:
        scenario = claim["scenario"]
        verdict = bundle.verdicts.get(scenario, "missing")
        lines.append(f"- {claim['id']} {scenario}: {verdict}. [C{claim['id'][1:]}]")
        errnos = sorted({int(n) for line in bundle.evidence(scenario) for n in ERRNO.findall(line)})
        if errnos:
            spelled = ", ".join(f"errno={n}" for n in errnos)
            lines.append(f"  Recorded refusals in this scenario carry {spelled}. [C{claim['id'][1:]}]")
    lines.append("")

    lines.append("## Sources")
    lines.append("")
    if bundle.commit:
        lines.append(f"The run was built from commit {bundle.commit}. [sources]")
    ids = sorted(set(_policy_ids(bundle)))
    for pid in ids:
        lines.append(f"The security policy in force during the run reported itself as {pid}. [bundle]")
    lines.append("")

    lines.append("## Boundary")
    lines.append("")
    lines.append("This note reports scenario verdicts and bundle identifiers only. [bundle]")
    lines.append("A PASS covers these scenarios on the two images above and nothing more. [gate]")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------

def stated_verdicts(text: str) -> set:
    """Every pass or fail word in a sentence, normalized to PASS or FAIL.

    A draft that says "all six scenarios passed" is making the same claim as
    one that writes PASS, so both forms go through the same comparison.
    """
    return {"PASS" if word.lower().startswith("pass") else "FAIL"
            for word in VERDICT.findall(text)}


def _policy_known(bundle: Bundle, pid: str) -> bool:
    """A policy id counts as present with or without its 0x prefix.

    secpol -Q prints the bare form and a status read prints the prefixed form,
    so both spellings of the same id are evidence of the same value.
    """
    text = bundle.text.lower()
    return pid.lower() in text or pid[2:].lower() in text


def split_sentences(text: str) -> list:
    return [s.strip() for s in SENTENCE_SPLIT.split(text.strip()) if s.strip()]


def split_units(markdown: str) -> list:
    units = []
    for lineno, raw in enumerate(markdown.splitlines(), 1):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            units.append({"line": lineno, "kind": "heading", "text": stripped.lstrip("#").strip()})
            continue
        kind = "bullet" if stripped.startswith(("- ", "* ")) else "text"
        body = stripped[2:].strip() if kind == "bullet" else stripped
        for sentence in split_sentences(body):
            units.append({"line": lineno, "kind": kind, "text": sentence})
    return units


def _defect(severity: str, code: str, detail: str) -> dict:
    return {"severity": severity, "code": code, "detail": detail}


def _mask(text: str, pattern: re.Pattern) -> str:
    return pattern.sub(" ", text)


def verify_unit(unit: dict, bundle: Bundle) -> dict:
    """Check one sentence. Returns the sentence with defects and what it used."""
    text = unit["text"]
    defects = []
    used = []

    for ch, name in FORBIDDEN_CHARS.items():
        if ch in text:
            defects.append(_defect("reject", "style", name))
    lowered = text.lower()
    for phrase in FORBIDDEN_PHRASES:
        if phrase in lowered:
            defects.append(_defect("reject", "style", phrase))
    for word in PERF_WORDS:
        if word in lowered:
            defects.append(_defect("reject", "performance", word))
    for match in NUMBER_WITH_UNIT.finditer(text):
        defects.append(_defect("reject", "performance", match.group(0)))
    for match in PERCENT.finditer(text):  # number-scan: allow
        defects.append(_defect("reject", "performance", match.group(0)))

    anchors = set(ANCHOR.findall(text))
    for anchor in sorted(anchors):
        if anchor.startswith("C"):
            claim = bundle.claim(anchor)
            if claim is None:
                defects.append(_defect("drop", "unknown-anchor", anchor))
                continue
            used.append(f"anchor:{anchor}")
            scenario = claim["scenario"]
            verdict = bundle.verdicts.get(scenario)
            if verdict is None:
                defects.append(_defect("drop", "missing-scenario", scenario))
                continue
            names = [n for n in bundle.verdicts if n in text]
            if names and scenario not in names:
                defects.append(_defect("drop", "anchor-mismatch",
                                       f"{anchor} is {scenario}, sentence names {', '.join(names)}"))
            stated = stated_verdicts(text)
            if stated and verdict not in stated:
                defects.append(_defect("drop", "contradiction",
                                       f"{scenario} is {verdict} in the bundle, sentence says {sorted(stated)[0]}"))
            expected = claim.get("expected_result", "PASS")
            if verdict != expected:
                defects.append(_defect("drop", "contradiction",
                                       f"{anchor} expects {expected}, bundle recorded {verdict}"))
        elif anchor == "gate":
            used.append("anchor:gate")
            stated = stated_verdicts(text)
            if stated and bundle.gate not in stated:
                defects.append(_defect("drop", "contradiction",
                                       f"gate is {bundle.gate}, sentence says {sorted(stated)[0]}"))
        else:
            used.append(f"anchor:{anchor}")

    for name, verdict in bundle.verdicts.items():
        if name in text:
            used.append(f"scenario:{name}")
            stated = stated_verdicts(text)
            if stated and verdict not in stated:
                defects.append(_defect("drop", "contradiction",
                                       f"{name} is {verdict}, sentence says {sorted(stated)[0]}"))

    for token in re.findall(r"\b[a-z0-9]+(?:-[a-z0-9]+)+\b", text):
        if token in bundle.verdicts:
            continue
        if token.startswith(SCENARIO_PREFIXES):
            defects.append(_defect("drop", "unknown-scenario", token))

    if "gate" in lowered and bundle.gate not in stated_verdicts(text) and stated_verdicts(text):
        defects.append(_defect("drop", "contradiction", f"gate is {bundle.gate}"))

    for h in HASH64.findall(text):
        if h not in bundle.hashes:
            defects.append(_defect("drop", "unverified-hash", h))
        else:
            used.append(f"sha256:{h[:12]}")
    for h in HASH40.findall(text):
        if not bundle.commit.startswith(h):
            defects.append(_defect("drop", "unverified-commit", h))
        else:
            used.append(f"commit:{h[:12]}")

    for pid in POLICY_ID.findall(text):
        if not _policy_known(bundle, pid):
            defects.append(_defect("drop", "unverified-policy-id", pid))
        else:
            used.append(f"policy:{pid}")
    for value in ERRNO.findall(text):
        if f"errno={value}" not in bundle.text:
            defects.append(_defect("drop", "unverified-errno", value))
        else:
            used.append(f"errno:{value}")
    for name, value in ERRNO_NAMES.items():
        if re.search(rf"\b{name}\b", text):
            if f"errno={value}" not in bundle.text:
                defects.append(_defect("drop", "unverified-errno", name))
            else:
                used.append(f"errno:{value}")

    counts = {"scenarios": len(bundle.scenarios), "scenario": len(bundle.scenarios),
              "files": len(bundle.manifest.get("files", [])), "file": len(bundle.manifest.get("files", [])),
              "claims": len(bundle.claims), "claim": len(bundle.claims),
              "checks": len(bundle.claims), "check": len(bundle.claims),
              "images": 2 if (bundle.image.get("normal") and bundle.image.get("negative")) else 1,
              "image": 2 if (bundle.image.get("normal") and bundle.image.get("negative")) else 1}
    for raw_count, noun in COUNT.findall(text):
        value = WORDS.get(raw_count.lower()) or int(raw_count)
        actual = counts.get(noun.lower())
        if actual is not None and value != actual:
            defects.append(_defect("drop", "count-mismatch", f"{raw_count} {noun}, bundle has {actual}"))
        else:
            used.append(f"count:{noun}={value}")

    for left, right in RATIO.findall(text):
        if int(right) != len(bundle.scenarios) or int(left) > int(right):
            defects.append(_defect("drop", "count-mismatch", f"{left}/{right} against {len(bundle.scenarios)} scenarios"))
        else:
            used.append(f"ratio:{left}/{right}")

    rest = text
    for pattern in (HASH64, HASH40, POLICY_ID, ERRNO, RATIO):
        rest = _mask(rest, pattern)
    for match in list(COUNT.finditer(rest)):
        rest = rest.replace(match.group(0), " ", 1)
    for match in list(VERSION.finditer(rest)):
        if match.group(0) in bundle.text:
            used.append(f"version:{match.group(0)}")
            rest = rest.replace(match.group(0), " ", 1)
        else:
            defects.append(_defect("drop", "unverified-version", match.group(0)))
            rest = rest.replace(match.group(0), " ", 1)
    allowed_numbers = {str(len(bundle.scenarios)), str(len(bundle.claims)),
                       str(len(bundle.manifest.get("files", [])))}
    for match in NUMBER.finditer(rest):
        raw = match.group(0)
        if raw in allowed_numbers:
            continue
        defects.append(_defect("drop", "unverified-number", raw))

    keep = not defects and bool(anchors or used)
    if not defects and not anchors and not used:
        defects.append(_defect("drop", "no-claim", "no scenario, hash, errno or count to check"))
    return {"line": unit["line"], "kind": unit["kind"], "text": text,
            "kept": keep, "defects": defects, "used": used}


def verify(markdown: str, bundle: Bundle) -> dict:
    units = split_units(markdown)
    results = []
    for unit in units:
        if unit["kind"] == "heading":
            defects = []
            lowered = unit["text"].lower()
            for ch, name in FORBIDDEN_CHARS.items():
                if ch in unit["text"]:
                    defects.append(_defect("reject", "style", name))
            for word in PERF_WORDS:
                if word in lowered:
                    defects.append(_defect("reject", "performance", word))
            stated = stated_verdicts(unit["text"])
            if stated and "gate" in lowered and bundle.gate not in stated:
                defects.append(_defect("drop", "contradiction", f"gate is {bundle.gate}"))
            results.append({"line": unit["line"], "kind": "heading", "text": unit["text"],
                            "kept": not defects, "defects": defects, "used": []})
            continue
        results.append(verify_unit(unit, bundle))

    kept = [r for r in results if r["kept"]]
    kept_sentences = [r for r in kept if r["kind"] != "heading"]
    dropped = [r for r in results if not r["kept"]]
    rejected = [r for r in results if any(d["severity"] == "reject" for d in r["defects"])]
    outcome = "REJECTED" if rejected or not kept_sentences else "VERIFIED"
    return {
        "outcome": outcome,
        "bundled_digest": bundle_digest(bundle),
        "gate": bundle.gate,
        "sentences": len([r for r in results if r["kind"] != "heading"]),
        "kept": len(kept),
        "dropped": len(dropped),
        "rejected": len(rejected),
        "units": results,
    }


def rebuild(markdown: str, report: dict) -> str:
    """Rebuild the note with only the sentences the verifier kept."""
    keep_lines: dict = {}
    for row in report["units"]:
        if row["kept"]:
            keep_lines.setdefault(row["line"], []).append(row)

    out = []
    source = markdown.splitlines()
    for lineno, raw in enumerate(source, 1):
        stripped = raw.strip()
        if not stripped:
            out.append("")
            continue
        if stripped.startswith("#"):
            if keep_lines.get(lineno):
                out.append(raw.rstrip())
            continue
        rows = keep_lines.get(lineno)
        if not rows:
            continue
        prefix = stripped[:2] if stripped.startswith(("- ", "* ")) else ""
        indent = "  " if raw.startswith("  ") and not prefix else ""
        joined = " ".join(row["text"] for row in rows)
        out.append(f"{indent}{prefix}{joined}".rstrip())
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def dropped_report(report: dict) -> str:
    lines = ["# Sentences removed from the draft", "",
             "| Line | Sentence | Reason |", "|---|---|---|"]
    for row in report["units"]:
        if row["kept"]:
            continue
        reasons = "; ".join(f"{d['severity']}: {d['code']} ({d['detail']})" for d in row["defects"])
        text = row["text"].replace("|", "\\|")
        lines.append(f"| {row['line']} | {text} | {reasons} |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# optional model provider
# ---------------------------------------------------------------------------

def proxy_draft(bundle: Bundle) -> str:
    """Ask an OpenAI compatible endpoint for a draft. Optional, never in tests.

    The answer is returned as text and goes through the same verifier, so a
    model can only make the note shorter, never more generous. Configuration:
    CORDON_LLM_URL (chat completions endpoint), CORDON_LLM_KEY, CORDON_LLM_MODEL.
    """
    import os
    import urllib.request

    url = os.environ.get("CORDON_LLM_URL")
    key = os.environ.get("CORDON_LLM_KEY")
    model = os.environ.get("CORDON_LLM_MODEL", "meta/llama-3.1-8b-instruct")
    if not url:
        raise BundleError("CORDON_LLM_URL is not set, so provider proxy cannot run")

    facts = {
        "gate": bundle.gate,
        "scenarios": bundle.verdicts,
        "claims": [{"id": c["id"], "scenario": c["scenario"], "expected": c.get("expected_result", "PASS")}
                   for c in bundle.claims],
        "commit": bundle.commit,
        "images": sorted(bundle.hashes),
        "policy_ids": sorted(set(POLICY_ID.findall(bundle.console + bundle.slog2))),
    }
    prompt = (
        "You write release notes from evidence. Use only the facts in this JSON. "
        "Write at most twelve short sentences in Markdown. End every sentence with the "
        "claim id it is about in square brackets, for example [C2], [gate] or [image]. "
        "Do not state any measurement, duration, share of runs, or performance result. "
        "Facts: " + json.dumps(facts, sort_keys=True)
    )
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0}).encode()
    request = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key or ''}",
    })
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode())
    return payload["choices"][0]["message"]["content"]
