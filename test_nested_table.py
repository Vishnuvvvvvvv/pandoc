"""
5-way comparison: markdownify vs html2text vs html-to-markdown vs
  html-to-markdown+BS4fix vs virtual-grid+html-to-markdown
Input: test-docs/table_test_cases.html
"""
import sys, re
sys.path.insert(0, '.')

import html2text
import html_to_markdown
from bs4 import BeautifulSoup
from markdownify import markdownify as to_md

with open('test-docs/table_test_cases.html', encoding='utf-8') as f:
    html = f.read()

# ── markdownify ───────────────────────────────────────────────────────────────
md1 = to_md(html, heading_style='ATX', bullets='-', strong_em_symbol='*')
md1 = re.sub(r'\n{3,}', '\n\n', md1)

# ── html2text ─────────────────────────────────────────────────────────────────
h = html2text.HTML2Text()
h.body_width = 0
h.unicode_snob = True
md2 = re.sub(r'\n{3,}', '\n\n', h.handle(html))

# ── BeautifulSoup pre-processor ───────────────────────────────────────────────
def preprocess_tables(raw_html: str) -> str:
    """
    BeautifulSoup pre-processor that fixes three HTML table issues:

    1. Rowspan cells   → content duplicated into each spanned row, preserving inner
                         HTML (bold, italic, links) via decode_contents()
    2. Nested tables   → replaced with compact text [Table: A | B / C | D]
                         (pipes escaped so they don't break outer pipe table)
    3. Multi-para cells → multiple <p> replaced by real <br> elements between
                          paragraphs (renders as line breaks in preview)
    """
    import copy
    from bs4 import NavigableString
    soup = BeautifulSoup(raw_html, 'html.parser')

    # ── Fix 1: expand rowspans, preserving inner HTML formatting ─────────────
    for table in soup.find_all('table'):
        rows = table.find_all('tr')
        # pending[vcol] = (inner_html_str, rows_remaining, tag_name)
        pending: dict = {}

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
                    # Re-parse stored HTML so all inline tags (<strong> etc.) are preserved
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
                        # Store INNER HTML (not plain text) to preserve formatting
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

    # ── Fix 2 & 3: per-cell fixes ─────────────────────────────────────────────
    for cell in soup.find_all(['td', 'th']):

        # Fix 2: flatten nested <table> → compact text with ESCAPED pipes (\|)
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
            # Escape | so the text doesn't break the outer pipe table
            compact = ' / '.join(row_texts).replace('|', r'\|')
            span = soup.new_tag('span')
            span.string = f'[Table: {compact}]' if row_texts else '[empty table]'
            nested.replace_with(span)

        # Fix 3: multiple <p> → real <br> elements (not escaped text)
        paras = cell.find_all('p', recursive=False)
        if len(paras) > 1:
            texts = [p.get_text(separator=' ', strip=True) for p in paras if p.get_text(strip=True)]
            cell.clear()
            for i, text in enumerate(texts):
                if i > 0:
                    cell.append(soup.new_tag('br'))
                cell.append(NavigableString(text))

    return str(soup)


# ── html-to-markdown (no fix) ─────────────────────────────────────────────────
md3 = re.sub(r'\n{3,}', '\n\n', html_to_markdown.convert(html).content)

# ── html-to-markdown + BeautifulSoup nested fix ───────────────────────────────
html_fixed = preprocess_tables(html)
md4 = re.sub(r'\n{3,}', '\n\n', html_to_markdown.convert(html_fixed).content)

# ── Approach 5: Virtual grid per-table + html-to-markdown for everything else ─
# Strategy:
#   1. Parse HTML, replace every <table> with a @@TABLE_N@@ placeholder
#   2. Convert the placeholder HTML through html-to-markdown (handles headings,
#      lists, inline formatting — no tables to worry about)
#   3. For each <table>, convert using the virtual 2D grid algorithm
#      (precise rowspan + colspan + nested + multi-para handling)
#   4. Paste GFM tables back at placeholder positions

def _cell_text_grid(cell) -> str:
    """Extract plain text from a <td>/<th>; multi-<p> joined with ' / '."""
    paras = cell.find_all('p')
    if paras:
        parts = [p.get_text(separator=' ', strip=True) for p in paras if p.get_text(strip=True)]
        return ' / '.join(parts).replace('|', r'\|').replace('\n', ' ').strip()
    return cell.get_text(separator=' ', strip=True).replace('|', r'\|').replace('\n', ' ').strip()


def _table_html_to_gfm(table_tag) -> str:
    """Convert a single BS4 <table> tag to a GFM pipe table via virtual 2D grid."""
    all_rows = table_tag.find_all('tr')
    if not all_rows:
        return ''

    max_cols = max(
        sum(int(c.get('colspan', 1)) for c in row.find_all(['th', 'td']))
        for row in all_rows
    )
    if not max_cols:
        return ''

    grid: list[list[str]] = []
    rowspan_carry: dict[int, tuple[str, int]] = {}
    prefix_lines: list[str] = []

    for row in all_rows:
        cells = row.find_all(['th', 'td'])

        # Materialise rowspan carries
        grid_row: list[str] = [''] * max_cols
        for c, (val, left) in list(rowspan_carry.items()):
            grid_row[c] = val
            if left <= 1:
                del rowspan_carry[c]
            else:
                rowspan_carry[c] = (val, left - 1)

        # Full-width single-cell → bold title above the table
        if len(cells) == 1 and int(cells[0].get('colspan', 1)) == max_cols:
            title = _cell_text_grid(cells[0])
            if title:
                prefix_lines.append(f'\n**{title}**')
            continue

        col = 0
        for cell in cells:
            while col < max_cols and grid_row[col] != '':
                col += 1
            if col >= max_cols:
                break
            colspan = int(cell.get('colspan', 1))
            rowspan = int(cell.get('rowspan', 1))

            # Handle nested tables inside this cell
            nested = cell.find('table')
            if nested:
                nested_rows = nested.find_all('tr')
                row_texts = [
                    ' | '.join(
                        c.get_text(separator=' ', strip=True)
                        for c in r.find_all(['td', 'th'])
                        if c.get_text(strip=True)
                    )
                    for r in nested_rows
                ]
                text = '[Table: ' + ' / '.join(r for r in row_texts if r) + ']'
            else:
                text = _cell_text_grid(cell)

            for i in range(min(colspan, max_cols - col)):
                slot = text if i == 0 else f'({text})'
                grid_row[col + i] = slot
                if rowspan > 1:
                    rowspan_carry[col + i] = (slot, rowspan - 1)
            col += colspan

        grid.append(grid_row)

    if not grid:
        return '\n'.join(prefix_lines)

    for r in grid:
        r += [''] * (max_cols - len(r))

    def _pipe(cells): return '| ' + ' | '.join(cells) + ' |'
    sep = '| ' + ' | '.join(['---'] * max_cols) + ' |'
    rows_md = [_pipe(grid[0]), sep, *(_pipe(r) for r in grid[1:])]
    return '\n'.join([*prefix_lines, *rows_md]) + '\n'


def virtual_grid_convert(raw_html: str) -> str:
    """Full pipeline: virtual grid for tables, html-to-markdown for everything else."""
    soup = BeautifulSoup(raw_html, 'html.parser')

    # Step 1: extract tables and replace with placeholders
    tables = soup.find_all('table', recursive=True)
    # Only top-level tables (not nested ones — they'll be handled inside _table_html_to_gfm)
    top_tables = [t for t in tables if not t.find_parent('table')]

    table_gfm: dict[str, str] = {}
    for i, tbl in enumerate(top_tables):
        placeholder = f'TABLEGFMPLACEHOLDER{i}ENDPLACEHOLDER'
        gfm = _table_html_to_gfm(tbl)
        table_gfm[placeholder] = gfm
        tbl.replace_with(soup.new_string(placeholder))

    # Step 2: convert the table-free HTML with html-to-markdown
    md = html_to_markdown.convert(str(soup)).content

    # Step 3: paste GFM tables back
    for placeholder, gfm in table_gfm.items():
        md = md.replace(placeholder, '\n\n' + gfm)

    return re.sub(r'\n{3,}', '\n\n', md)


md5 = virtual_grid_convert(html)

# ── Save all outputs ──────────────────────────────────────────────────────────
outputs = [
    ('markdownify',            md1),
    ('html2text',              md2),
    ('html_to_markdown',       md3),
    ('html_to_markdown_fixed', md4),
    ('virtual_grid',           md5),
]
for name, md in outputs:
    with open(f'test-docs/output_{name}.md', 'w', encoding='utf-8') as f:
        f.write(md)

# ── Print summary ─────────────────────────────────────────────────────────────
def count_tags(md): return len(re.findall(r'<[a-zA-Z/][^>]*>', md))

lines = [
    '=' * 65,
    '5-WAY COMPARISON',
    '=' * 65,
    '',
    f"{'Library':<40} {'Lines':>6}  {'HTML tags left':>14}",
    '-' * 65,
    f"{'markdownify':<40} {len(md1.splitlines()):>6}  {count_tags(md1):>14}",
    f"{'html2text':<40} {len(md2.splitlines()):>6}  {count_tags(md2):>14}",
    f"{'html-to-markdown':<40} {len(md3.splitlines()):>6}  {count_tags(md3):>14}",
    f"{'html-to-markdown + BS4 fix':<40} {len(md4.splitlines()):>6}  {count_tags(md4):>14}",
    f"{'virtual grid + html-to-markdown':<40} {len(md5.splitlines()):>6}  {count_tags(md5):>14}",
    '=' * 65,
    '',
]
sys.stdout.buffer.write('\n'.join(lines).encode())

for label, md in [
    ('markdownify',                     md1),
    ('html2text',                       md2),
    ('html-to-markdown',                md3),
    ('html-to-markdown + BS4 fix',      md4),
    ('virtual grid + html-to-markdown', md5),
]:
    sep = f"\n{'='*65}\n{label} OUTPUT:\n{'='*65}\n"
    sys.stdout.buffer.write(sep.encode())
    sys.stdout.buffer.write(md.encode('utf-8', errors='replace'))
