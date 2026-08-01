"""
docx_to_md.py — Convert a DOCX file to clean Markdown.

Pipeline:
  DOCX → Pandoc (HTML) → preprocess_tables (BS4) → html-to-markdown → .md

Usage:
  python docx_to_md.py path/to/document.docx
  python docx_to_md.py path/to/document.docx path/to/output.md

Dependencies:
  pip install pypandoc html-to-markdown beautifulsoup4
  (Pandoc binary must also be installed: https://pandoc.org/installing.html)
"""

from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

import html_to_markdown
import pypandoc
from bs4 import BeautifulSoup, NavigableString


# ══════════════════════════════════════════════════════════════════════════════
# BeautifulSoup pre-processor
# ══════════════════════════════════════════════════════════════════════════════

def preprocess_tables(raw_html: str) -> str:
    """
    Fixes HTML table issues before Markdown conversion.

    Strategy:
      - Tables that contain a nested <table> inside any cell are KEPT AS RAW HTML.
        Raw HTML renders as a real table-in-table in VS Code preview, GitHub, etc.
      - All other tables go through the normal pipe-table path with three fixes:
          1. Rowspan  → content duplicated, inner HTML preserved (bold/italic/links)
          2. Multi-p  → multiple <p> replaced by real <br> elements (line breaks)
    """
    soup = BeautifulSoup(raw_html, 'html.parser')

    # ── Step A: pull out tables that have nested tables → raw HTML placeholders ──
    raw_html_blocks: dict[str, str] = {}
    for table in soup.find_all('table'):
        # Only top-level tables (skip inner tables — they stay inside the outer HTML)
        if table.find_parent('table'):
            continue
        if table.find('table'):          # this outer table contains a nested table
            placeholder = f'RAWHTMLTABLE{id(table)}END'
            raw_html_blocks[placeholder] = str(table)
            table.replace_with(soup.new_string(f'\n\n{placeholder}\n\n'))

    # ── Step B: fix remaining (non-nested) tables ─────────────────────────────

    # Fix 1: expand rowspans, preserving inner HTML formatting
    for table in soup.find_all('table'):
        rows = table.find_all('tr')
        pending: dict = {}   # vcol → (inner_html, rows_remaining, tag_name)

        for row in rows:
            cells = list(row.find_all(['td', 'th'], recursive=False))
            inserts = []
            max_vcol = max(pending.keys(), default=-1) + 1 + sum(
                int(c.get('colspan', 1)) for c in cells
            )
            vcol, cell_index = 0, 0

            while vcol < max_vcol or cell_index < len(cells):
                if vcol in pending:
                    inner_html, remaining, tag_name = pending[vcol]
                    new_cell = soup.new_tag(tag_name)
                    frag = BeautifulSoup(inner_html, 'html.parser')
                    for child in frag.children:
                        new_cell.append(copy.deepcopy(child))
                    ref = cells[cell_index] if cell_index < len(cells) else None
                    inserts.append(('before' if ref else 'append', ref or row, new_cell))
                    if remaining > 1:
                        pending[vcol] = (inner_html, remaining - 1, tag_name)
                    else:
                        del pending[vcol]
                    vcol += 1
                elif cell_index < len(cells):
                    cell = cells[cell_index]
                    rowspan = int(cell.get('rowspan', 1))
                    colspan  = int(cell.get('colspan', 1))
                    if rowspan > 1:
                        inner_html = cell.decode_contents()
                        for i in range(colspan):
                            pending[vcol + i] = (inner_html, rowspan - 1, cell.name)
                        del cell['rowspan']
                    vcol += colspan
                    cell_index += 1
                else:
                    break

            for action, ref, new_cell in inserts:
                if action == 'before':
                    ref.insert_before(new_cell)
                else:
                    ref.append(new_cell)

    # Fix 2: multiple <p> per cell → real <br> elements (renders as line breaks)
    for cell in soup.find_all(['td', 'th']):
        paras = cell.find_all('p', recursive=False)
        if len(paras) > 1:
            texts = [p.get_text(separator=' ', strip=True) for p in paras if p.get_text(strip=True)]
            cell.clear()
            for i, text in enumerate(texts):
                if i > 0:
                    cell.append(soup.new_tag('br'))
                cell.append(NavigableString(text))

    # ── Step C: convert to Markdown, then paste raw HTML tables back ───────────
    md = html_to_markdown.convert(str(soup)).content
    for placeholder, html_table in raw_html_blocks.items():
        md = md.replace(placeholder, f'\n\n{html_table}\n\n')

    return re.sub(r'\n{3,}', '\n\n', md)


def convert_docx_to_md(docx_path: str | Path) -> str:
    """
    Convert a .docx file to clean Markdown.

    Steps:
      1. Pandoc converts DOCX → HTML
      2. preprocess_tables (BS4):
           - Tables with nested tables → kept as raw HTML (renders in preview)
           - Plain tables → rowspan expanded + multi-para fixed → GFM pipe table
      3. Tidy up excess blank lines

    Returns:
      str — clean Markdown string
    """
    raw_html = pypandoc.convert_file(str(docx_path), 'html', extra_args=['--wrap=none'])
    md = preprocess_tables(raw_html)
    md = md.replace(r'\$', '$')
    md = re.sub(r'\n{3,}', '\n\n', md)
    return md


def convert_html_to_md(html_path: str | Path) -> str:
    """
    Convert a raw .html file to clean Markdown using the same pipeline
    (skips the Pandoc DOCX→HTML step).
    """
    raw_html = Path(html_path).read_text(encoding='utf-8')
    md = preprocess_tables(raw_html)
    md = md.replace(r'\$', '$')
    md = re.sub(r'\n{3,}', '\n\n', md)
    return md



# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        print("Usage: python docx_to_md.py input.[docx|html] [output.md]")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    if not input_path.exists():
        print(f"Error: file not found — {input_path}", file=sys.stderr)
        sys.exit(1)
    
    suffix = input_path.suffix.lower()
    if suffix not in ('.docx', '.html', '.htm'):
        print(f"Error: expected a .docx or .html file, got '{suffix}'", file=sys.stderr)
        sys.exit(1)

    # Default output: same folder, same name, .md extension
    out_path = Path(sys.argv[2]) if len(sys.argv) >= 3 else input_path.with_suffix('.md')

    print(f"Converting: {input_path}")
    if suffix == '.docx':
        md = convert_docx_to_md(input_path)
    else:
        md = convert_html_to_md(input_path)
        
    out_path.write_text(md, encoding='utf-8')

    # Report
    remaining = len(re.findall(r'<(?!br\b)[a-zA-Z/][^>]*>', md))
    print(f"Output   : {out_path}")
    print(f"Lines    : {len(md.splitlines())}")
    print(f"HTML tags: {remaining} (excluding intentional <br> in table cells)")


if __name__ == '__main__':
    main()
