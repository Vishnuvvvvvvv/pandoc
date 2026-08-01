import sys, re
sys.path.insert(0, '.')
from md_html_cleaner import clean_markdown

with open('uploads/497bae26-6db7-4ce2-868c-545af0d36694/497bae26-6db7-4ce2-868c-545af0d36694.md', encoding='utf-8') as f:
    md = f.read()

result = clean_markdown(md)

with open('test-docs/final_clean_output.md', 'w', encoding='utf-8') as f:
    f.write(result)

remaining = len(re.findall(r'<[a-zA-Z/][^>]*>', result))
sys.stdout.buffer.write(f"HTML tags remaining: {remaining}\n".encode())
sys.stdout.buffer.write(f"Lines: {len(result.splitlines())}\n\n".encode())
sys.stdout.buffer.write(result.encode('utf-8', errors='replace'))
