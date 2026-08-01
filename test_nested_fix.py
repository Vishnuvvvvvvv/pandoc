"""
Test: pre-process nested tables before html-to-markdown conversion.

Strategy:
  1. Use BeautifulSoup to find every <table> nested inside a <td> or <th>.
  2. Convert the inner table to compact text: "A | B / C | D" (rows separated by " / ")
  3. Replace the inner <table> with a <span> containing that text.
  4. Convert the cleaned HTML normally — the outer table now renders cleanly.
"""
import sys, re
sys.path.insert(0, '.')

from bs4 import BeautifulSoup
import html_to_markdown


def flatten_nested_tables(html: str) -> str:
    """
    Pre-processor: replaces any <table> found inside a <td> or <th>
    with a compact text representation so the outer table converts cleanly.

    Inner table renders as:  [Header A | Header B / Row1A | Row1B / Row2A | Row2B]
    """
    soup = BeautifulSoup(html, 'html.parser')

    for cell in soup.find_all(['td', 'th']):
        for nested in cell.find_all('table'):
            rows = nested.find_all('tr')
            row_texts = []
            for row in rows:
                cells = row.find_all(['td', 'th'])
                cell_texts = [c.get_text(separator=' ', strip=True) for c in cells if c.get_text(strip=True)]
                if cell_texts:
                    row_texts.append(' | '.join(cell_texts))

            text_repr = ' / '.join(row_texts) if row_texts else ''
            span = soup.new_tag('span')
            span.string = f'[Table: {text_repr}]' if text_repr else '[empty table]'
            nested.replace_with(span)

    return str(soup)


# ── Load HTML ──────────────────────────────────────────────────────────────────
with open('test-docs/table_test_cases.html', encoding='utf-8') as f:
    html = f.read()

# ── Step 1: Pre-process — flatten nested tables ────────────────────────────────
html_clean = flatten_nested_tables(html)

# ── Step 2: Convert HTML → Markdown ───────────────────────────────────────────
result = html_to_markdown.convert(html_clean)
md = re.sub(r'\n{3,}', '\n\n', result.content)

# ── Save and report ────────────────────────────────────────────────────────────
with open('test-docs/output_nested_fixed.md', 'w', encoding='utf-8') as f:
    f.write(md)

remaining = len(re.findall(r'<[a-zA-Z/][^>]*>', md))
sys.stdout.buffer.write(f"Tags remaining: {remaining}\nLines: {len(md.splitlines())}\n\n".encode())
sys.stdout.buffer.write(md.encode('utf-8', errors='replace'))
