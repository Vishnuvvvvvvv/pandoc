"""
md_html_cleaner.py
══════════════════
Standalone post-processor for Pandoc-generated Markdown files.

PROBLEM
───────
When Pandoc converts a complex Word document (.docx) to Markdown it emits
raw HTML for structures it cannot express in plain GFM:
  • <table> with colspan / rowspan (merged cells)
  • <p> tags inside table cells with multiple paragraphs
  • <u>, <span style="…"> for inline formatting
  • &nbsp;, &amp; and other HTML entities

This module removes ALL of that in two clean passes, producing a
100 % pure Markdown file with zero HTML tags — ideal for LLM embeddings
and RAG pipelines where HTML tokens waste context and break chunking.

USAGE
─────
  # As a library (drop into any project):
  from md_html_cleaner import clean_markdown
  clean_md = clean_markdown(raw_pandoc_output)

  # From the command line:
  python md_html_cleaner.py input.md output.md
  python md_html_cleaner.py input.md           # overwrites in-place

DEPENDENCIES
────────────
  pip install beautifulsoup4
  (html, re are standard-library — no other dependencies)

PASS OVERVIEW
─────────────
  Pass 1 — _convert_html_tables()
    • Parses every <table> block with BeautifulSoup
    • Full-width merged header rows  →  **Bold title** line above the table
    • All other rows                 →  GFM | pipe | table |
    • Multi-paragraph <p> cells      →  joined with " / "
    • colspan cells                  →  label repeated across spanned columns
    • rowspan cells                  →  value repeated in every covered row

  Pass 2 — _strip_remaining_html()
    • <br> / <br/>     →  newline
    • <!-- comment -->  →  removed
    • <u>text</u>       →  text
    • <span>text</span> →  text
    • Any HTML tag      →  stripped, inner text preserved
    • &nbsp; &amp; etc. →  decoded to real Unicode characters
    • [text]{.cls}      →  text  (Pandoc native span syntax)
    • Code fences ```   →  NEVER touched
"""

from __future__ import annotations

import html as _html_mod
import re
import sys
from pathlib import Path

try:
    from bs4 import BeautifulSoup as _BS
    _BS4_OK = True
except ImportError:
    _BS4_OK = False


# ══════════════════════════════════════════════════════════════════════════════
# PASS 1 — Convert HTML <table> blocks to GFM pipe tables
# ══════════════════════════════════════════════════════════════════════════════

def _convert_html_tables(md: str) -> str:
    """
    Replaces every HTML <table> block with a clean GFM pipe table.
    Falls back to leaving the table untouched if BeautifulSoup is not installed
    or if parsing fails for any individual table.
    """
    if not _BS4_OK:
        print(
            "[md_html_cleaner] WARNING: beautifulsoup4 is not installed.\n"
            "  HTML <table> blocks will NOT be converted.\n"
            "  Fix: pip install beautifulsoup4",
            file=sys.stderr,
        )
        return md

    TABLE_RE = re.compile(r"<table[\s\S]*?</table>", re.IGNORECASE)

    def _cell_text(cell) -> str:
        """Extract clean text from a <td>/<th>, joining <p> blocks with ' / '."""
        paras = cell.find_all("p")
        if paras:
            parts = [p.get_text(separator=" ", strip=True) for p in paras if p.get_text(strip=True)]
            text = " / ".join(parts)
        else:
            text = cell.get_text(separator=" ", strip=True)
        # Escape pipe chars so they don't break the GFM pipe table syntax
        return text.replace("|", "\\|").replace("\n", " ").strip()

    def _table_to_md(table_html: str) -> str:
        try:
            soup = _BS(table_html, "html.parser")
            table = soup.find("table")
            if not table:
                return table_html

            all_rows = table.find_all("tr")
            if not all_rows:
                return table_html

            # ── Determine the true column count ──
            max_cols = 0
            for row in all_rows:
                cols = sum(
                    int(c.get("colspan", 1)) for c in row.find_all(["th", "td"])
                )
                max_cols = max(max_cols, cols)
            if max_cols == 0:
                return table_html

            # ── Build a virtual 2-D grid, expanding colspan + rowspan ──
            grid: list[list[str]] = []
            rowspan_carry: dict[int, tuple[str, int]] = {}  # col → (text, rows_left)
            prefix_lines: list[str] = []  # bold titles emitted ABOVE the pipe table

            for row in all_rows:
                cells = row.find_all(["th", "td"])

                # Materialise carried rowspan values for this row
                grid_row: list[str] = [""] * max_cols
                for c, (val, left) in list(rowspan_carry.items()):
                    grid_row[c] = val
                    if left <= 1:
                        del rowspan_carry[c]
                    else:
                        rowspan_carry[c] = (val, left - 1)

                # Full-width single-cell rows become bold titles above the table
                if len(cells) == 1 and int(cells[0].get("colspan", 1)) == max_cols:
                    title = _cell_text(cells[0])
                    if title:
                        prefix_lines.append(f"\n**{title}**")
                    continue  # don't add to grid

                col = 0
                for cell in cells:
                    # Advance past rowspan-filled slots
                    while col < max_cols and grid_row[col] != "":
                        col += 1
                    if col >= max_cols:
                        break

                    colspan = int(cell.get("colspan", 1))
                    rowspan = int(cell.get("rowspan", 1))
                    text = _cell_text(cell)

                    for i in range(min(colspan, max_cols - col)):
                        # First column of a span shows the text; extras show it in parens
                        slot = text if i == 0 else f"({text})"
                        grid_row[col + i] = slot
                        if rowspan > 1:
                            rowspan_carry[col + i] = (slot, rowspan - 1)

                    col += colspan

                grid.append(grid_row)

            if not grid:
                return "\n".join(prefix_lines)

            # Pad every row to uniform width
            for r in grid:
                while len(r) < max_cols:
                    r.append("")

            def _pipe(cells: list[str]) -> str:
                return "| " + " | ".join(cells) + " |"

            lines = list(prefix_lines)
            lines.append(_pipe(grid[0]))                             # header row
            lines.append("| " + " | ".join(["---"] * max_cols) + " |")  # separator
            for r in grid[1:]:
                lines.append(_pipe(r))

            return "\n".join(lines) + "\n"

        except Exception as exc:
            # Never crash the pipeline — fall back to raw HTML for this table
            print(f"[md_html_cleaner] WARNING: table conversion failed ({exc}) — kept as HTML", file=sys.stderr)
            return table_html

    return TABLE_RE.sub(lambda m: _table_to_md(m.group(0)), md)


# ══════════════════════════════════════════════════════════════════════════════
# PASS 2 — Strip every remaining HTML tag and entity
# ══════════════════════════════════════════════════════════════════════════════

def _strip_remaining_html(md: str) -> str:
    """
    Removes all remaining HTML from a Markdown string.
    Code fences (``` ... ```) and inline code (` ... `) are never modified.
    """
    # Split on fenced/inline code blocks so we never touch code content
    FENCE_RE = re.compile(r"(```[\s\S]*?```|`[^`\n]+`)", re.MULTILINE)
    segments = FENCE_RE.split(md)

    result: list[str] = []
    for i, seg in enumerate(segments):
        if i % 2 == 1:
            # Odd-indexed segments are code blocks — leave completely untouched
            result.append(seg)
            continue

        s = seg
        # 1. <br> / <br/> → newline
        s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
        # 2. Remove HTML comments
        s = re.sub(r"<!--[\s\S]*?-->", "", s)
        # 3. Strip ALL remaining HTML tags (keep their inner text content)
        s = re.sub(r"<[^>]+>", "", s)
        # 4. Decode HTML entities  (&nbsp; → " ", &amp; → "&", etc.)
        s = _html_mod.unescape(s)
        # 5. Pandoc native span syntax: [text]{.classname} → text
        s = re.sub(r"\[([^\]]+)\]\{[^}]+\}", r"\1", s)
        # 6. Collapse 3+ consecutive blank lines to 2 (keep document tidy)
        s = re.sub(r"\n{3,}", "\n\n", s)

        result.append(s)

    return "".join(result)


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API — call this single function
# ══════════════════════════════════════════════════════════════════════════════

def clean_markdown(md: str) -> str:
    """
    Run both cleaning passes on a Pandoc-generated Markdown string and return
    a pure Markdown string with zero HTML tags or entities.

    Parameters
    ----------
    md : str
        Raw Markdown output from Pandoc (or any other converter).

    Returns
    -------
    str
        Clean Markdown with all HTML removed and structure preserved.

    Example
    -------
    >>> import pypandoc
    >>> raw_md = pypandoc.convert_file("report.docx", "gfm", extra_args=["--wrap=none"])
    >>> clean_md = clean_markdown(raw_md)
    >>> open("report_clean.md", "w").write(clean_md)
    """
    md = _convert_html_tables(md)   # Pass 1: <table> → pipe table
    md = _strip_remaining_html(md)  # Pass 2: everything else
    return md


# ══════════════════════════════════════════════════════════════════════════════
# CLI USAGE:  python md_html_cleaner.py input.md [output.md]
# ══════════════════════════════════════════════════════════════════════════════

def _cli():
    if len(sys.argv) < 2:
        print("Usage: python md_html_cleaner.py input.md [output.md]")
        sys.exit(1)

    input_path  = Path(sys.argv[1])
    output_path = Path(sys.argv[2]) if len(sys.argv) >= 3 else input_path

    if not input_path.exists():
        print(f"Error: file not found — {input_path}", file=sys.stderr)
        sys.exit(1)

    raw_md   = input_path.read_text(encoding="utf-8")
    clean_md = clean_markdown(raw_md)
    output_path.write_text(clean_md, encoding="utf-8")

    # Quick report
    import re as _re
    remaining_tags     = len(_re.findall(r"<[a-zA-Z/][^>]*>", clean_md))
    remaining_entities = len(_re.findall(r"&[a-zA-Z]+;",      clean_md))
    print(f"[OK] Written to: {output_path}")
    print(f"     Lines:             {len(clean_md.splitlines())}")
    print(f"     HTML tags left:    {remaining_tags}")
    print(f"     HTML entities left:{remaining_entities}")


if __name__ == "__main__":
    _cli()
