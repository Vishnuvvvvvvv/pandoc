"""
docx_pipeline.py — Simple DOCX → Markdown pipeline.

  ┌─────────────────────────────────────────────────────────┐
  │                    PIPELINE FLOW                        │
  │                                                         │
  │  DOCX file                                              │
  │    │                                                    │
  │    ▼  ◀── Step 0: Detect page breaks in DOCX XML       │
  │  _inject_page_markers()                                 │
  │    │   Injects XPAGEBREAKMARKERX text at break points  │
  │    ▼                                                    │
  │  ┌───────────────────────────────────────────────────┐  │
  │  │  Step 1: Pandoc converts DOCX → GFM Markdown     │  │
  │  │  (direct conversion, no HTML intermediary)        │  │
  │  └───────────────────────────────────────────────────┘  │
  │    │                                                    │
  │    ▼                                                    │
  │  ┌───────────────────────────────────────────────────┐  │
  │  │  Step 2: Replace page markers with separators     │  │
  │  │  XPAGEBREAKMARKERX2 → --- / <!-- Page 2 -->       │  │
  │  └───────────────────────────────────────────────────┘  │
  │    │                                                    │
  │    ▼                                                    │
  │  .md file                                               │
  └─────────────────────────────────────────────────────────┘

Usage:
  python docx_pipeline.py input.docx [output.md]

Dependencies:
  pip install pypandoc
  Pandoc binary: https://pandoc.org/installing.html
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pypandoc

# ── Word XML namespace & page break marker ────────────────────────────────────
_W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
_PAGE_MARKER = 'XPAGEBREAKMARKERX'


# ══════════════════════════════════════════════════════════════════════════════
# STEP 0: PAGE BREAK DETECTION & MARKER INJECTION
# ══════════════════════════════════════════════════════════════════════════════
#
# Page break info lives inside the DOCX zip → word/document.xml
# Two XML patterns indicate an explicit page break:
#   1. <w:br w:type="page"/>       → author pressed Ctrl+Enter
#   2. <w:pageBreakBefore/>        → style forces break before paragraph
#
# We inject marker text (XPAGEBREAKMARKERX2, etc.) into the DOCX XML
# BEFORE Pandoc sees it. Pandoc preserves the text, and in Step 2 we
# replace it with clean <!-- Page N --> comments.
# ══════════════════════════════════════════════════════════════════════════════

def _inject_page_markers(docx_path: Path) -> Path:
    """
    Parse DOCX XML, find explicit page breaks, inject marker paragraphs.
    Returns path to a temporary DOCX with markers injected.
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

                    # Register namespaces to keep output clean
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
                        insertions = []

                        for i, elem in enumerate(list(body)):
                            found_break = False

                            # Pattern 1: <w:br w:type="page"/>
                            for br in elem.iter(f'{{{_W_NS}}}br'):
                                if br.get(f'{{{_W_NS}}}type') == 'page':
                                    found_break = True
                                    break

                            # Pattern 2: <w:pageBreakBefore/>
                            if not found_break:
                                pPr = elem.find(f'{{{_W_NS}}}pPr')
                                if pPr is not None and pPr.find(f'{{{_W_NS}}}pageBreakBefore') is not None:
                                    found_break = True

                            if found_break:
                                page_num += 1
                                marker_p = ET.SubElement(ET.Element('dummy'), f'{{{_W_NS}}}p')
                                marker_r = ET.SubElement(marker_p, f'{{{_W_NS}}}r')
                                marker_t = ET.SubElement(marker_r, f'{{{_W_NS}}}t')
                                marker_t.text = f'{_PAGE_MARKER}{page_num}'
                                marker_t.set('xml:space', 'preserve')
                                insertions.append((i, marker_p))

                        # Insert in reverse to preserve indices
                        for idx, marker_p in reversed(insertions):
                            body.insert(idx, marker_p)

                    data = ET.tostring(root, encoding='unicode', xml_declaration=True).encode('utf-8')

                zout.writestr(item, data)

    return tmp_docx


# ══════════════════════════════════════════════════════════════════════════════
# MAIN PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

def convert_docx(docx_path: str | Path, page_markers: bool = True) -> str:
    """
    Convert a .docx file to GFM Markdown using Pandoc.

    Steps:
      0. (Optional) Inject page break markers into DOCX XML
      1. Pandoc converts DOCX → GFM Markdown directly
      2. Replace page markers with <!-- Page N --> separators

    Args:
      docx_path:    Path to the .docx file
      page_markers: If True, detect explicit page breaks and insert separators

    Returns:
      str — GFM Markdown string
    """
    docx_path = Path(docx_path)
    tmp_docx = None

    try:
        # Step 0: inject page markers into DOCX XML
        if page_markers:
            try:
                tmp_docx = _inject_page_markers(docx_path)
                use_path = tmp_docx
            except Exception:
                use_path = docx_path
        else:
            use_path = docx_path

        # Step 1: Pandoc DOCX → GFM (direct conversion)
        md = pypandoc.convert_file(str(use_path), 'gfm', extra_args=['--wrap=none'])

        # Cleanup
        md = md.replace(r'\$', '$')
        md = re.sub(r'\n{3,}', '\n\n', md)
        # Pandoc GFM sometimes inserts blank lines between pipe table rows — remove them
        md = re.sub(r'(\|[^\n]+\|)\n\n(\|[^\n]+\|)', r'\1\n\2', md)

        # Step 2: Replace page markers with clean separators
        if page_markers and _PAGE_MARKER in md:
            md = f'<!-- Page 1 -->\n\n{md}'
            md = re.sub(
                rf'{_PAGE_MARKER}(\d+)',
                lambda m: f'\n\n---\n\n<!-- Page {m.group(1)} -->',
                md
            )
            md = re.sub(r'\n{3,}', '\n\n', md)

        return md

    finally:
        if tmp_docx and tmp_docx.exists():
            shutil.rmtree(tmp_docx.parent, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        print("Usage: python docx_pipeline.py input.docx [output.md]")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    if not input_path.exists():
        print(f"Error: file not found — {input_path}", file=sys.stderr)
        sys.exit(1)

    if input_path.suffix.lower() != '.docx':
        print(f"Error: expected .docx, got '{input_path.suffix}'", file=sys.stderr)
        sys.exit(1)

    out_path = Path(sys.argv[2]) if len(sys.argv) >= 3 else input_path.with_suffix('.md')

    print(f"Converting: {input_path}")
    md = convert_docx(input_path)
    out_path.write_text(md, encoding='utf-8')

    lines = len(md.split('\n'))
    pages = len(re.findall(r'<!-- Page \d+ -->', md))
    print(f"Output   : {out_path}")
    print(f"Lines    : {lines}")
    if pages:
        print(f"Pages    : {pages}")


if __name__ == '__main__':
    main()
