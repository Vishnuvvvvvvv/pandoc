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
import zipfile
import tempfile
import shutil
from pathlib import Path
from xml.etree import ElementTree as ET

import html_to_markdown
import pypandoc
from bs4 import BeautifulSoup, NavigableString

# Word XML namespace
_W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
_PAGE_MARKER = 'XPAGEBREAKMARKERX'


# ══════════════════════════════════════════════════════════════════════════════
# BeautifulSoup pre-processor
# ══════════════════════════════════════════════════════════════════════════════

def preprocess_tables(raw_html: str) -> str:
    """
    Fixes HTML table issues before Markdown conversion.

    Strategy:
      - Tables with nested tables are FLATTENED into sequential elements:
          * Single-cell rows → bold headings
          * Non-table content inside cells → paragraphs
          * Inner tables → promoted to top-level (become pipe tables)
        Result: zero HTML in output, all content preserved as pure Markdown.
      - All other tables go through the normal pipe-table path with fixes:
          1. Rowspan  → content duplicated, inner HTML preserved (bold/italic/links)
          2. Multi-p  → multiple <p> replaced by real <br> elements (line breaks)
    """
    soup = BeautifulSoup(raw_html, 'html.parser')

    # ── Step A: flatten tables that contain nested tables ─────────────────────
    for table in list(soup.find_all('table')):
        # Only process top-level tables
        if table.find_parent('table'):
            continue
        if not table.find('table'):
            continue  # no nesting — skip, handled normally

        # Decompose: walk each row/cell and emit sequential elements
        replacement_elements = []

        # Only iterate rows that belong directly to this outer table
        for row in table.find_all('tr', recursive=False):
            pass
        all_rows = [
            tr for tr in table.find_all('tr')
            if tr.find_parent('table') is table
        ]

        for row in all_rows:
            cells = row.find_all(['td', 'th'], recursive=False)

            for cell in cells:
                # Check if this cell has a nested table
                nested_tables = cell.find_all('table', recursive=False)

                if not nested_tables and not cell.find('table'):
                    # Simple cell — emit its content
                    text = cell.get_text(separator=' ', strip=True)
                    if text:
                        # Single-cell rows with bold → treat as a heading
                        strong = cell.find('strong')
                        if strong and strong.get_text(strip=True) == text:
                            h = soup.new_tag('h3')
                            h.string = text
                            replacement_elements.append(h)
                        else:
                            p = soup.new_tag('p')
                            p.string = text
                            replacement_elements.append(p)
                else:
                    # Cell has nested table(s) — emit non-table content + tables
                    for child in cell.children:
                        if hasattr(child, 'name') and child.name == 'table':
                            # Promote inner table to top level (it will become a pipe table)
                            replacement_elements.append(copy.deepcopy(child))
                        elif hasattr(child, 'name') and child.name == 'p':
                            text = child.get_text(separator=' ', strip=True)
                            if text:
                                p = soup.new_tag('p')
                                p.string = text
                                replacement_elements.append(p)
                        elif isinstance(child, NavigableString):
                            text = child.strip()
                            if text:
                                p = soup.new_tag('p')
                                p.string = text
                                replacement_elements.append(p)

        # Replace the outer table with the flattened elements
        for elem in reversed(replacement_elements):
            table.insert_after(copy.deepcopy(elem))
        table.decompose()

    # ── Step B: fix remaining tables (rowspan + multi-para) ───────────────────

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

    # ── Step C: convert to Markdown ───────────────────────────────────────────
    md = html_to_markdown.convert(str(soup)).content
    return re.sub(r'\n{3,}', '\n\n', md)



def _inject_page_markers(docx_path: Path) -> Path:
    """
    Parse the DOCX XML to find explicit page breaks and inject marker
    paragraphs before each one. Returns path to a temp DOCX with markers.

    Detects two types of explicit page breaks:
      1. <w:br w:type="page"/>  — run-level break (Ctrl+Enter)
      2. <w:pageBreakBefore/>   — paragraph property (style-based)
    """
    tmp_dir = Path(tempfile.mkdtemp())
    tmp_docx = tmp_dir / docx_path.name

    with zipfile.ZipFile(docx_path, 'r') as zin:
        with zipfile.ZipFile(tmp_docx, 'w') as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)

                if item.filename == 'word/document.xml':
                    tree = ET.parse(zin.open(item.filename))
                    root = tree.getroot()

                    # Register namespace to avoid ns0: prefix in output
                    for prefix, uri in [
                        ('w', _W_NS),
                        ('r', 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'),
                        ('wp', 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'),
                        ('mc', 'http://schemas.openxmlformats.org/markup-compatibility/2006'),
                        ('w14', 'http://schemas.microsoft.com/office/word/2010/wordml'),
                        ('w15', 'http://schemas.microsoft.com/office/word/2012/wordml'),
                        ('wps', 'http://schemas.microsoft.com/office/word/2010/wordprocessingShape'),
                    ]:
                        ET.register_namespace(prefix, uri)

                    body = root.find(f'{{{_W_NS}}}body')
                    if body is not None:
                        page_num = 1
                        insertions = []  # (index, marker_paragraph)

                        for i, elem in enumerate(list(body)):
                            found_break = False

                            # Type 1: <w:br w:type="page"/> inside a run
                            for br in elem.iter(f'{{{_W_NS}}}br'):
                                if br.get(f'{{{_W_NS}}}type') == 'page':
                                    found_break = True
                                    break

                            # Type 2: <w:pageBreakBefore/> in paragraph properties
                            if not found_break:
                                pPr = elem.find(f'{{{_W_NS}}}pPr')
                                if pPr is not None and pPr.find(f'{{{_W_NS}}}pageBreakBefore') is not None:
                                    found_break = True

                            if found_break:
                                page_num += 1
                                # Create marker paragraph: <w:p><w:r><w:t>XPAGEBREAKMARKERX2</w:t></w:r></w:p>
                                marker_p = ET.SubElement(ET.Element('dummy'), f'{{{_W_NS}}}p')
                                marker_r = ET.SubElement(marker_p, f'{{{_W_NS}}}r')
                                marker_t = ET.SubElement(marker_r, f'{{{_W_NS}}}t')
                                marker_t.text = f'{_PAGE_MARKER}{page_num}'
                                marker_t.set('xml:space', 'preserve')
                                insertions.append((i, marker_p))

                        # Insert markers in reverse order to preserve indices
                        for idx, marker_p in reversed(insertions):
                            body.insert(idx, marker_p)

                    data = ET.tostring(root, encoding='unicode', xml_declaration=True).encode('utf-8')

                zout.writestr(item, data)

    return tmp_docx


def convert_docx_to_md(docx_path: str | Path, page_markers: bool = True) -> str:
    """
    Convert a .docx file to clean Markdown.

    Steps:
      1. (Optional) Inject page break markers into DOCX XML
      2. Pandoc converts DOCX → HTML
      3. preprocess_tables (BS4) fixes complex tables
      4. Replace page markers with --- Page N --- separators
      5. Tidy up excess blank lines

    Args:
      docx_path:    Path to the .docx file
      page_markers: If True, detect explicit page breaks and insert separators

    Returns:
      str — clean Markdown string
    """
    docx_path = Path(docx_path)
    tmp_docx = None

    try:
        if page_markers:
            try:
                tmp_docx = _inject_page_markers(docx_path)
                use_path = tmp_docx
            except Exception:
                use_path = docx_path  # fallback if marker injection fails
        else:
            use_path = docx_path

        raw_html = pypandoc.convert_file(str(use_path), 'html', extra_args=['--wrap=none'])
        md = preprocess_tables(raw_html)
        md = md.replace(r'\$', '$')
        md = re.sub(r'\n{3,}', '\n\n', md)

        # Replace page markers with clean separators
        if page_markers:
            # Add Page 1 header at the very top
            md = f'<!-- Page 1 -->\n\n{md}'
            md = re.sub(
                rf'{_PAGE_MARKER}(\d+)',
                lambda m: f'\n\n---\n\n<!-- Page {m.group(1)} -->',
                md
            )

        return md

    finally:
        if tmp_docx and tmp_docx.exists():
            shutil.rmtree(tmp_docx.parent, ignore_errors=True)


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
