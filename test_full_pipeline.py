"""
Full pipeline test on a real .docx file:
  DOCX → Pandoc HTML → preprocess_tables (BS4) → html-to-markdown → clean .md

Run: python test_full_pipeline.py test-docs/demo.docx
"""
import sys, re
sys.path.insert(0, '.')

import pypandoc
import html_to_markdown
from bs4 import BeautifulSoup, NavigableString
from pathlib import Path


# ══════════════════════════════════════════════════════════════════════════════
# BeautifulSoup pre-processor
# ══════════════════════════════════════════════════════════════════════════════
def preprocess_tables(raw_html: str) -> str:
    """
    Fix three HTML table issues before Markdown conversion:
      1. Rowspan  → duplicate content into each spanned row
      2. Nested   → replace inner <table> with [Table: A | B / C | D]
      3. Multi-p  → join multiple <p> with real <br> elements (renders as newlines)
    """
    soup = BeautifulSoup(raw_html, 'html.parser')

    # ── Fix 1: expand rowspans ────────────────────────────────────────────────
    for table in soup.find_all('table'):
        rows = table.find_all('tr')
        pending: dict = {}   # vcol → (text, remaining, tag_name)

        for row in rows:
            cells = list(row.find_all(['td', 'th'], recursive=False))
            inserts = []
            max_vcol = max(pending.keys(), default=-1) + 1 + sum(
                int(c.get('colspan', 1)) for c in cells
            )
            vcol, cell_index = 0, 0

            while vcol < max_vcol or cell_index < len(cells):
                if vcol in pending:
                    text, remaining, tag_name = pending[vcol]
                    new_cell = soup.new_tag(tag_name)
                    new_cell.string = text
                    ref = cells[cell_index] if cell_index < len(cells) else None
                    inserts.append(('before' if ref else 'append', ref or row, new_cell))
                    if remaining > 1:
                        pending[vcol] = (text, remaining - 1, tag_name)
                    else:
                        del pending[vcol]
                    vcol += 1
                elif cell_index < len(cells):
                    cell = cells[cell_index]
                    rowspan = int(cell.get('rowspan', 1))
                    colspan  = int(cell.get('colspan', 1))
                    if rowspan > 1:
                        text = cell.get_text(separator=' ', strip=True)
                        for i in range(colspan):
                            pending[vcol + i] = (text, rowspan - 1, cell.name)
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

    # ── Fix 2 & 3: per-cell fixes ─────────────────────────────────────────────
    for cell in soup.find_all(['td', 'th']):

        # Fix 2: nested tables → compact text
        for nested in cell.find_all('table'):
            nested_rows = nested.find_all('tr')
            row_texts = [
                ' | '.join(
                    c.get_text(separator=' ', strip=True)
                    for c in r.find_all(['td', 'th'])
                    if c.get_text(strip=True)
                )
                for r in nested_rows
            ]
            row_texts = [r for r in row_texts if r]
            span = soup.new_tag('span')
            span.string = f'[Table: {" / ".join(row_texts)}]' if row_texts else '[empty table]'
            nested.replace_with(span)

        # Fix 3: multiple <p> → real <br> elements between them
        paras = cell.find_all('p', recursive=False)
        if len(paras) > 1:
            texts = [p.get_text(separator=' ', strip=True) for p in paras if p.get_text(strip=True)]
            cell.clear()
            for i, text in enumerate(texts):
                if i > 0:
                    cell.append(soup.new_tag('br'))
                cell.append(NavigableString(text))

    return str(soup)


# ══════════════════════════════════════════════════════════════════════════════
# Main pipeline
# ══════════════════════════════════════════════════════════════════════════════
def convert_docx(docx_path: str) -> str:
    """DOCX → Pandoc HTML → preprocess → html-to-markdown → clean MD."""
    raw_html  = pypandoc.convert_file(docx_path, 'html', extra_args=['--wrap=none'])
    clean_html = preprocess_tables(raw_html)
    md = html_to_markdown.convert(clean_html).content
    md = md.replace(r'\$', '$')
    md = re.sub(r'\n{3,}', '\n\n', md)
    return md


if __name__ == '__main__':
    docx = sys.argv[1] if len(sys.argv) > 1 else 'test-docs/demo.docx'
    out  = Path(docx).with_suffix('.output.md')

    md = convert_docx(docx)
    out.write_text(md, encoding='utf-8')

    remaining_tags = len(re.findall(r'<(?!br\b)[a-zA-Z/][^>]*>', md))  # ignore intentional <br>
    sys.stdout.buffer.write(
        f"Input : {docx}\n"
        f"Output: {out}\n"
        f"Lines : {len(md.splitlines())}\n"
        f"Non-br HTML tags remaining: {remaining_tags}\n\n".encode()
    )
    sys.stdout.buffer.write(md.encode('utf-8', 'replace'))
