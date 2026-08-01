"""
md_html_cleaner.py
══════════════════
Post-processor for Pandoc-generated Markdown files.

Approach: hybrid.
  - HTML <table> blocks  →  converted via markdownify (no custom logic)
  - All remaining tags   →  stripped via markdownify on isolated segments
  - Non-HTML Markdown    →  never touched

USAGE (library)
───────────────
  from md_html_cleaner import clean_markdown
  clean_md = clean_markdown(raw_pandoc_output)

USAGE (CLI)
───────────
  python md_html_cleaner.py input.md [output.md]

DEPENDENCIES
────────────
  pip install markdownify
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

from markdownify import markdownify as _to_md

log = logging.getLogger(__name__)

# Pre-compiled patterns
_TABLE_RE    = re.compile(r"<table[\s\S]*?</table>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[a-zA-Z/][^>]*>")          # detects any HTML tag
_FENCE_RE    = re.compile(r"(```[\s\S]*?```|`[^`\n]+`)", re.MULTILINE)
_EXCESS_NL   = re.compile(r"\n{3,}")

_MD_OPTS = dict(heading_style="ATX", bullets="-", strong_em_symbol="*")


def _has_html(text: str) -> bool:
    return bool(_HTML_TAG_RE.search(text))


def _clean_segment(seg: str) -> str:
    """
    Convert any HTML tags/entities in a non-code Markdown segment.
    Only segments that actually contain HTML are passed through markdownify;
    pure Markdown segments are returned unchanged.
    """
    if not _has_html(seg):
        return seg                         # already clean — don't touch it
    converted = _to_md(seg, **_MD_OPTS)
    return _EXCESS_NL.sub("\n\n", converted)


def clean_markdown(md: str) -> str:
    """
    Remove all HTML from Pandoc-generated Markdown, preserving existing
    Markdown syntax (bold, italic, pipe tables, etc.) exactly as-is.

    Strategy
    ────────
    1. Replace <table> blocks first (markdownify converts them to pipe tables).
    2. Split remaining text on code fences to protect code blocks.
    3. On each non-code segment: if it contains HTML tags, run markdownify;
       otherwise leave it completely untouched.

    Parameters
    ----------
    md  : str — Raw Markdown from Pandoc (may contain HTML fragments).

    Returns
    -------
    str — Pure Markdown with zero HTML tags or entities.

    Example
    -------
    >>> import pypandoc
    >>> raw = pypandoc.convert_file("report.docx", "gfm", extra_args=["--wrap=none"])
    >>> open("report.md", "w").write(clean_markdown(raw))
    """
    # Pass 1: handle <table> blocks (always contain HTML)
    md = _TABLE_RE.sub(lambda m: _to_md(m.group(0), **_MD_OPTS), md)

    # Pass 2: handle any remaining HTML tags in non-code segments only
    segments = _FENCE_RE.split(md)
    return "".join(
        seg if i % 2 else _clean_segment(seg)
        for i, seg in enumerate(segments)
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    if len(sys.argv) < 2:
        sys.exit("Usage: python md_html_cleaner.py input.md [output.md]")

    src = Path(sys.argv[1])
    dst = Path(sys.argv[2]) if len(sys.argv) >= 3 else src

    if not src.exists():
        sys.exit(f"Error: file not found — {src}")

    clean = clean_markdown(src.read_text(encoding="utf-8"))
    dst.write_text(clean, encoding="utf-8")

    remaining = len(_HTML_TAG_RE.findall(clean))
    print(f"Written : {dst}")
    print(f"Lines   : {len(clean.splitlines())}")
    print(f"HTML tags remaining: {remaining}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    _cli()
