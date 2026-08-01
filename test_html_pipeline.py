import sys
sys.path.insert(0, '.')
import pypandoc
from markdownify import markdownify as to_md
import re

# Step 1: Pandoc outputs pure HTML (no mixed content)
html = pypandoc.convert_file(
    'test-docs/complex_test_document.docx',
    'html',
    extra_args=['--wrap=none']
)

# Step 2: markdownify converts the whole thing cleanly
md = to_md(
    html,
    heading_style="ATX",
    bullets="-",
    strong_em_symbol="*",
)

# Step 3: Tidy up excessive blank lines
md = re.sub(r'\n{3,}', '\n\n', md)

with open('test-docs/html_pipeline_output.md', 'w', encoding='utf-8') as f:
    f.write(md)

remaining_tags = len(re.findall(r'<[a-zA-Z/][^>]*>', md))
sys.stdout.buffer.write(f"HTML tags remaining: {remaining_tags}\n".encode())
sys.stdout.buffer.write(f"Lines: {len(md.splitlines())}\n\n".encode())
sys.stdout.buffer.write(md[:3000].encode('utf-8', errors='replace'))
