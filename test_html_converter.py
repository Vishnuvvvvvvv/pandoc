import sys, re
sys.path.insert(0, '.')
from pandoc_pipeline_router import _convert_html_tables, _strip_remaining_html

with open('uploads/497bae26-6db7-4ce2-868c-545af0d36694/497bae26-6db7-4ce2-868c-545af0d36694.md', encoding='utf-8') as f:
    md = f.read()

# Run both passes
md = _convert_html_tables(md)
md = _strip_remaining_html(md)

with open('test-docs/final_clean_output.md', 'w', encoding='utf-8') as f:
    f.write(md)

# Count any remaining HTML-like things
html_tags    = re.findall(r'<[a-zA-Z/][^>]*>', md)
html_entities = re.findall(r'&[a-zA-Z]+;', md)
pandoc_spans = re.findall(r'\{[.#][^}]+\}', md)

print(f"Remaining HTML tags:     {len(html_tags)}")
print(f"Remaining HTML entities: {len(html_entities)}")
print(f"Remaining Pandoc spans:  {len(pandoc_spans)}")
print(f"Total lines:             {len(md.splitlines())}")
print()
print("=== FULL OUTPUT ===")
# Write to stdout safely
sys.stdout.buffer.write(md.encode('utf-8', errors='replace'))
