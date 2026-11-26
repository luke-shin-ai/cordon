#!/usr/bin/env python3
"""Render a codelab written in the QNX codelabs metadata format to static HTML.

The QNX codelabs repository renders markdown with claat (claat export -f html).
That tool is a Go binary that is not always present, and the bonus entry only
needs a public URL, so this script produces the same page shape from the same
markdown with nothing but Python. The output is a directory you can publish on
GitHub Pages as it is.

  python3 render.py --check                 validate every codelab, render nothing
  python3 render.py --out site              validate and render into site/
  python3 render.py --check --root markdown only look in one directory tree

Metadata keys, from the QNX codelabs README: id, title, summary, categories,
tags, platforms, published, featured, status, authors. id must equal the file
name, tags carries exactly one level, published is YYYY-MM-DD.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_ROOT = HERE / "markdown"
DEFAULT_OUT = HERE / "site"

REQUIRED_KEYS = ("id", "title", "summary", "categories", "tags", "published", "status")
LEVELS = ("beginner", "intermediate", "advanced")
STATUSES = ("published", "draft", "hidden")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

INLINE_CODE = re.compile(r"`([^`]+)`")
BOLD = re.compile(r"\*\*([^*]+)\*\*")
LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


@dataclass
class Step:
    title: str
    duration: str
    body: list = field(default_factory=list)


@dataclass
class Codelab:
    meta: dict
    title_heading: str
    steps: list
    source: Path


def parse(text: str) -> tuple:
    """Split the metadata block from the body. Returns (meta, heading, body lines)."""
    lines = text.splitlines()
    meta = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        if line.lstrip().startswith("#"):
            break
        if ":" not in line:
            break
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
        index += 1
    body = lines[index:]
    heading = ""
    for line in body:
        if line.startswith("# "):
            heading = line[2:].strip()
            break
    return meta, heading, body


def parse_steps(body: list) -> list:
    steps: list = []
    current = None
    for line in body:
        if line.startswith("# "):
            continue
        if line.startswith("## "):
            current = Step(title=line[3:].strip(), duration="")
            steps.append(current)
            continue
        if current is None:
            continue
        match = re.match(r"^Duration:\s*(.+)$", line.strip())
        if match and not current.body:
            current.duration = match.group(1).strip()
            continue
        current.body.append(line)
    return steps


def load(path: Path) -> Codelab:
    text = path.read_text(encoding="utf-8")
    meta, heading, body = parse(text)
    return Codelab(meta=meta, title_heading=heading, steps=parse_steps(body), source=path)


def validate(codelab: Codelab) -> list:
    problems = []
    meta = codelab.meta
    for key in REQUIRED_KEYS:
        if not meta.get(key):
            problems.append(f"missing metadata key: {key}")
    if meta.get("id") and meta["id"] != codelab.source.stem:
        problems.append(f"id {meta['id']} does not match file name {codelab.source.stem}")
    if meta.get("tags") and meta["tags"] not in LEVELS:
        problems.append(f"tags must be one of {', '.join(LEVELS)}, got {meta['tags']}")
    if meta.get("status") and meta["status"] not in STATUSES:
        problems.append(f"status must be one of {', '.join(STATUSES)}, got {meta['status']}")
    if meta.get("published") and not DATE.match(meta["published"]):
        problems.append(f"published must be YYYY-MM-DD, got {meta['published']}")
    categories = [c.strip() for c in meta.get("categories", "").split(",") if c.strip()]
    if categories and len(categories) > 2:
        problems.append("categories carries at most two labels")
    if meta.get("featured") and meta["featured"].lower() not in ("true", "false"):
        problems.append("featured must be true or false")
    if not codelab.title_heading:
        problems.append("no H1 title in the body")
    if len(codelab.steps) < 2:
        problems.append("a codelab needs at least two sections")
    for step in codelab.steps:
        text = "\n".join(step.body)
        if text.count("```") % 2:
            problems.append(f"unbalanced code fence in section: {step.title}")
    return problems


def inline(text: str) -> str:
    out = html.escape(text)
    out = INLINE_CODE.sub(lambda m: f"<code>{m.group(1)}</code>", out)
    out = BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", out)
    out = LINK.sub(lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>', out)
    return out


def render_body(lines: list) -> str:
    out = []
    paragraph: list = []
    bullets: list = []
    numbered: list = []
    code: list = []
    language = ""
    in_code = False

    def flush_paragraph() -> None:
        if paragraph:
            out.append("<p>" + " ".join(inline(line.strip()) for line in paragraph) + "</p>")
            paragraph.clear()

    def flush_list() -> None:
        if bullets:
            out.append("<ul>" + "".join(f"<li>{inline(item)}</li>" for item in bullets) + "</ul>")
            bullets.clear()
        if numbered:
            out.append("<ol>" + "".join(f"<li>{inline(item)}</li>" for item in numbered) + "</ol>")
            numbered.clear()

    for line in lines:
        stripped = line.strip()
        fence = re.match(r"^```(\w*)$", stripped)
        if in_code:
            if fence:
                body = html.escape("\n".join(code))
                cls = f' class="language-{language}"' if language else ""
                out.append(f"<pre><code{cls}>{body}</code></pre>")
                code.clear()
                in_code = False
            else:
                code.append(line)
            continue
        if fence:
            flush_paragraph()
            flush_list()
            in_code = True
            language = fence.group(1)
            continue
        if not stripped:
            flush_paragraph()
            flush_list()
            continue
        if stripped.startswith("- "):
            flush_paragraph()
            flush_list()
            bullets.append(stripped[2:])
            continue
        match = re.match(r"^\d+\.\s+(.*)$", stripped)
        if match:
            flush_paragraph()
            flush_list()
            numbered.append(match.group(1))
            continue
        if stripped.startswith("### "):
            flush_paragraph()
            flush_list()
            out.append(f"<h3>{inline(stripped[4:])}</h3>")
            continue
        if stripped.startswith(">"):
            flush_paragraph()
            flush_list()
            out.append(f"<blockquote>{inline(stripped[1:].strip())}</blockquote>")
            continue
        paragraph.append(line)
    if in_code:
        out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
    flush_paragraph()
    flush_list()
    return "\n".join(out)


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<nav><a href="index.html">All codelabs</a></nav>
<main>
<header class="meta">
<p class="summary">{summary}</p>
<table>
<tr><th>Categories</th><td>{categories}</td></tr>
<tr><th>Level</th><td>{tags}</td></tr>
<tr><th>Platforms</th><td>{platforms}</td></tr>
<tr><th>Published</th><td>{published}</td></tr>
<tr><th>Author</th><td>{authors}</td></tr>
</table>
</header>
<article>
<h1>{title}</h1>
{body}
</article>
</main>
<footer><p>Built with the QNX codelabs metadata format.</p></footer>
</body>
</html>
"""

INDEX = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cordon codelabs</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<main>
<h1>Codelabs</h1>
{cards}
</main>
<footer><p>Built with the QNX codelabs metadata format.</p></footer>
</body>
</html>
"""

CARD = """<section class="card">
<h2><a href="{id}.html">{title}</a></h2>
<p>{summary}</p>
<p class="tags">{categories} | {tags} | {platforms} | {published}</p>
</section>"""

STYLE = """body { font-family: system-ui, sans-serif; line-height: 1.6; margin: 0; color: #1b1b1b; }
main { max-width: 46rem; margin: 0 auto; padding: 2rem 1rem; }
nav { padding: 0.75rem 1rem; border-bottom: 1px solid #ddd; }
a { color: #0b5cad; }
h1 { font-size: 1.9rem; }
h2 { margin-top: 2.4rem; border-top: 1px solid #e2e2e2; padding-top: 1.2rem; }
pre { background: #f5f5f5; padding: 1rem; overflow-x: auto; border-left: 3px solid #cc092f; }
code { font-family: ui-monospace, monospace; font-size: 0.95em; }
blockquote { border-left: 3px solid #ddd; margin: 1rem 0; padding-left: 1rem; color: #444; }
table { border-collapse: collapse; }
th, td { text-align: left; padding: 0.2rem 0.8rem 0.2rem 0; }
.card { border: 1px solid #e2e2e2; padding: 0 1rem; margin: 1.5rem 0; }
.tags, .summary { color: #555; }
footer { text-align: center; color: #777; padding: 2rem; font-size: 0.9rem; }
"""


def render(codelab: Codelab) -> str:
    parts = []
    for step in codelab.steps:
        parts.append(f'<section id="{slug(step.title)}">')
        parts.append(f"<h2>{inline(step.title)}</h2>")
        if step.duration:
            parts.append(f'<p class="duration">Estimated time: {html.escape(step.duration)}</p>')
        parts.append(render_body(step.body))
        parts.append("</section>")
    meta = codelab.meta
    return PAGE.format(
        title=html.escape(meta.get("title", "")),
        summary=html.escape(meta.get("summary", "")),
        categories=html.escape(meta.get("categories", "")),
        tags=html.escape(meta.get("tags", "")),
        platforms=html.escape(meta.get("platforms", "")),
        published=html.escape(meta.get("published", "")),
        authors=html.escape(meta.get("authors", "")),
        body="\n".join(parts),
    )


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--check", action="store_true", help="validate only")
    args = parser.parse_args(argv)

    files = sorted(args.root.rglob("*.md"))
    files = [f for f in files if f.name != "markdown-template.md"]
    if not files:
        print(f"no codelab markdown under {args.root}", file=sys.stderr)
        return 1

    failed = 0
    rendered = []
    for path in files:
        codelab = load(path)
        problems = validate(codelab)
        status = codelab.meta.get("status", "?")
        if problems:
            failed += 1
            print(f"FAIL {path}")
            for problem in problems:
                print(f"  {problem}")
            continue
        print(f"OK   {path.name}  id={codelab.meta['id']}  steps={len(codelab.steps)}  status={status}")
        rendered.append(codelab)

    if args.check:
        return 1 if failed else 0
    if failed:
        print("not rendering while a codelab fails validation", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "style.css").write_text(STYLE, encoding="utf-8")
    cards = []
    for codelab in rendered:
        meta = codelab.meta
        (args.out / f"{meta['id']}.html").write_text(render(codelab), encoding="utf-8")
        cards.append(CARD.format(
            id=html.escape(meta["id"]),
            title=html.escape(meta["title"]),
            summary=html.escape(meta["summary"]),
            categories=html.escape(meta["categories"]),
            tags=html.escape(meta["tags"]),
            platforms=html.escape(meta.get("platforms", "")),
            published=html.escape(meta["published"]),
        ))
    (args.out / "index.html").write_text(INDEX.format(cards="\n".join(cards)), encoding="utf-8")
    print(f"rendered {len(rendered)} codelab(s) into {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
