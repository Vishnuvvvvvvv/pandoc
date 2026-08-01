import html_to_markdown, re, sys
with open('test-docs/table_test_cases.html', encoding='utf-8') as f:
    html = f.read()

result = html_to_markdown.convert(html)
md = result.content
md = re.sub(r'\n{3,}', '\n\n', md)

with open('test-docs/output_html_to_markdown.md', 'w', encoding='utf-8') as f:
    f.write(md)

remaining = len(re.findall(r'<[a-zA-Z/][^>]*>', md))
sys.stdout.buffer.write(f"Tags remaining: {remaining}\nLines: {len(md.splitlines())}\n\n".encode())
sys.stdout.buffer.write(md.encode('utf-8', errors='replace'))
