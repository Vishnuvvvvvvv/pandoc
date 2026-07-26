"""
test_excel_all.py
─────────────────
Run the Excel parser against all .xlsx / .csv files in test-docs/
and print the Markdown output for each sheet.

Usage:
    python test_excel_all.py                    # test all files in test-docs/
    python test_excel_all.py myfile.xlsx        # test a specific file
"""

import sys
from pathlib import Path

from excel_parsing import (
    _extract_xlsx,
    _extract_csv,
    _is_form_sheet,
    _form_to_markdown,
    _tabular_to_markdown,
)

SEP = "=" * 70


def process(file_path: Path):
    print(f"\n{SEP}")
    print(f"FILE: {file_path.name}")
    print(SEP)

    suffix = file_path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        sheets = _extract_xlsx(file_path)
    elif suffix == ".csv":
        sheets = _extract_csv(file_path)
    else:
        print(f"  Skipped (unsupported: {suffix})")
        return

    for s in sheets:
        name = s["name"]
        grid = s["grid"]
        rsv  = s.get("meta", {}).get("row_span_vals", set())
        form = _is_form_sheet(grid)
        renderer = "FORM" if form else "TABULAR"

        print(f"\n{'-'*70}")
        print(f"  Sheet: {name!r}  |  renderer: {renderer}  |  rows: {len(grid)}")
        print(f"{'-'*70}\n")

        if not grid:
            print("  (empty sheet)")
            continue

        md = (_form_to_markdown(grid, name, rsv)
              if form else _tabular_to_markdown(grid, name))
        print(md)


def main():
    if len(sys.argv) > 1:
        targets = [Path(p) for p in sys.argv[1:]]
    else:
        test_dir = Path("test-docs")
        targets = sorted(test_dir.glob("*.xlsx")) + sorted(test_dir.glob("*.csv"))
        targets = [t for t in targets if not t.name.startswith("~$")]  # skip open-file locks
        if not targets:
            print(f"No .xlsx / .csv files found in {test_dir}/")
            return

    for t in targets:
        process(t)


if __name__ == "__main__":
    main()
