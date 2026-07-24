"""Focused test: Actions section and Version Control sheet."""
from pathlib import Path
from excel_parsing import _extract_xlsx, _is_form_sheet, _form_to_markdown, _tabular_to_markdown

sheets = _extract_xlsx(Path("test-docs/vfm_assessment_complex.xlsx"))

for s in sheets:
    if s["name"] not in ("Exec Summary", "Version Control"):
        continue
    form = _is_form_sheet(s["grid"])
    row_span_vals = s.get("meta", {}).get("row_span_vals", set())
    md = (_form_to_markdown(s["grid"], s["name"], row_span_vals)
          if form else _tabular_to_markdown(s["grid"], s["name"]))

    if s["name"] == "Exec Summary":
        # Print only from Actions onwards
        start = md.find("## Actions")
        print("=== Exec Summary: Actions -> end ===")
        print(md[start:] if start != -1 else "(Actions not found)")
    else:
        print(f"\n=== {s['name']} ===")
        print(md)
