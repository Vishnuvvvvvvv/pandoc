"""
excel_parsing.py
────────────────
FastAPI router for Excel / CSV extraction:
  - openpyxl   → .xlsx / .xlsm  (native, merged-cell aware)
  - xlrd        → .xls           (legacy binary format)
  - csv (stdlib)→ .csv

Produces per-sheet Markdown tables + a structured semantic JSON.

Mount in any FastAPI app:
    from excel_parsing import router as excel_router
    app.include_router(excel_router, prefix="/excel", tags=["Excel Pipeline"])
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

load_dotenv()

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("excel_pipeline")

# ── Config ─────────────────────────────────────────────────────────────────────
OUTPUT_DIR  = Path(os.getenv("EXCEL_OUTPUT_DIR", "./uploads/excel"))
STAGING_DIR = OUTPUT_DIR / "_staging"
MAX_ROWS_PER_SHEET = int(os.getenv("EXCEL_MAX_ROWS", "5000"))   # safety cap
MAX_COLS_PER_SHEET = int(os.getenv("EXCEL_MAX_COLS", "200"))
ALLOWED_EXT = {".xlsx", ".xlsm", ".xls", ".csv"}

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
STAGING_DIR.mkdir(parents=True, exist_ok=True)

# ── In-memory job store ────────────────────────────────────────────────────────
_jobs: dict[str, dict[str, Any]] = {}
_executor = ThreadPoolExecutor(max_workers=2)

# ── Pydantic schemas ───────────────────────────────────────────────────────────
class UploadResponse(BaseModel):
    job_id: str
    status: str
    message: str

class SheetSummary(BaseModel):
    name: str
    rows: int
    cols: int
    empty: bool

class JobStatus(BaseModel):
    job_id: str
    status: str
    document: Optional[str] = None
    sheets: Optional[list[SheetSummary]] = None
    total_tables: Optional[int] = None
    markdown_preview: Optional[str] = None
    markdown_path: Optional[str] = None
    semantic_json_path: Optional[str] = None
    error: Optional[str] = None

# ── Router ─────────────────────────────────────────────────────────────────────
router = APIRouter()


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════
class _SafeEncoder(json.JSONEncoder):
    def default(self, obj):
        try:
            return super().default(obj)
        except TypeError:
            return str(obj)


def _cell_str(value: Any) -> str:
    """Safely convert any cell value to a plain string."""
    if value is None:
        return ""
    if isinstance(value, float):
        # Drop .0 suffix for whole numbers
        return str(int(value)) if value == int(value) else str(value)
    return str(value)


def _grid_to_markdown(grid: list[list[str]], sheet_name: str) -> str:
    """
    Convert a 2-D list of strings to a GFM table.
    First row is treated as the header. If the sheet is empty, returns a note.
    Pipe characters inside cells are escaped.
    """
    # Strip completely empty trailing rows/cols
    while grid and all(c == "" for c in grid[-1]):
        grid.pop()
    if not grid:
        return f"*Sheet `{sheet_name}` is empty.*\n"

    def _row_md(cells: list[str]) -> str:
        escaped = [c.replace("|", "\\|").replace("\n", " ") for c in cells]
        return "| " + " | ".join(escaped) + " |"

    lines = [_row_md(grid[0])]
    lines.append("| " + " | ".join(["---"] * len(grid[0])) + " |")
    for row in grid[1:]:
        # Pad / trim row to match header width
        padded = row[:len(grid[0])] + [""] * max(0, len(grid[0]) - len(row))
        lines.append(_row_md(padded))
    return "\n".join(lines)


def _trim_grid(grid: list[list[str]]) -> list[list[str]]:
    """Remove fully-empty trailing rows and columns."""
    # Trim empty rows from the bottom
    while grid and all(c == "" for c in grid[-1]):
        grid.pop()
    if not grid:
        return grid
    # Trim empty columns from the right
    max_col = max(
        (max((i for i, c in enumerate(row) if c != ""), default=-1) for row in grid),
        default=-1,
    )
    if max_col < 0:
        return []
    return [row[:max_col + 1] for row in grid]


# ══════════════════════════════════════════════════════════════════════════════
# Per-format sheet extractors
# ══════════════════════════════════════════════════════════════════════════════
def _extract_xlsx(file_path: Path) -> list[dict[str, Any]]:
    """
    Extract all sheets from .xlsx / .xlsm using openpyxl.
    Handles merged cells by filling the top-left value across the merge range.
    Returns list of {name, grid, meta}.
    """
    try:
        import openpyxl
    except ImportError:
        raise RuntimeError("openpyxl not installed. Run: uv add openpyxl")

    wb = openpyxl.load_workbook(str(file_path), data_only=True, read_only=False)
    sheets = []

    for ws in wb.worksheets:
        # Expand merged cells: fill top-left value into all covered cells
        merge_map: dict[tuple[int, int], str] = {}
        for merge_range in ws.merged_cells.ranges:
            top_left_cell = ws.cell(merge_range.min_row, merge_range.min_col)
            val = _cell_str(top_left_cell.value)
            for row in range(merge_range.min_row, merge_range.max_row + 1):
                for col in range(merge_range.min_col, merge_range.max_col + 1):
                    merge_map[(row, col)] = val

        grid: list[list[str]] = []
        for r_idx, row in enumerate(ws.iter_rows(
            max_row=min(ws.max_row or 0, MAX_ROWS_PER_SHEET),
            max_col=min(ws.max_column or 0, MAX_COLS_PER_SHEET),
        )):
            cells = []
            for cell in row:
                val = merge_map.get((cell.row, cell.column), _cell_str(cell.value))
                cells.append(val)
            grid.append(cells)

        grid = _trim_grid(grid)
        sheets.append({
            "name": ws.title,
            "grid": grid,
            "meta": {
                "max_row": ws.max_row,
                "max_col": ws.max_column,
                "has_merged_cells": bool(ws.merged_cells.ranges),
            },
        })

    wb.close()
    return sheets


def _extract_xls(file_path: Path) -> list[dict[str, Any]]:
    """Extract all sheets from legacy .xls using xlrd."""
    try:
        import xlrd
    except ImportError:
        raise RuntimeError("xlrd not installed. Run: uv add xlrd")

    wb = xlrd.open_workbook(str(file_path))
    sheets = []

    for sheet_name in wb.sheet_names():
        ws = wb.sheet_by_name(sheet_name)
        grid: list[list[str]] = []
        for r in range(min(ws.nrows, MAX_ROWS_PER_SHEET)):
            row = [_cell_str(ws.cell_value(r, c))
                   for c in range(min(ws.ncols, MAX_COLS_PER_SHEET))]
            grid.append(row)
        grid = _trim_grid(grid)
        sheets.append({
            "name": sheet_name,
            "grid": grid,
            "meta": {"max_row": ws.nrows, "max_col": ws.ncols, "has_merged_cells": False},
        })

    return sheets


def _extract_csv(file_path: Path) -> list[dict[str, Any]]:
    """Parse a CSV file as a single-sheet workbook."""
    with open(file_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        grid = [[_cell_str(c) for c in row]
                for i, row in enumerate(reader) if i < MAX_ROWS_PER_SHEET]
    grid = _trim_grid(grid)
    return [{"name": file_path.stem, "grid": grid,
             "meta": {"max_row": len(grid), "max_col": len(grid[0]) if grid else 0,
                      "has_merged_cells": False}}]


# ══════════════════════════════════════════════════════════════════════════════
# Core pipeline
# ══════════════════════════════════════════════════════════════════════════════
def _process_excel(file_path: Path) -> dict[str, Any]:
    suffix = file_path.suffix.lower()
    name = file_path.stem
    out_dir = OUTPUT_DIR / name
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info("Processing Excel: %s", file_path.name)

    # ── Extract sheets ──────────────────────────────────────────────────────
    try:
        if suffix in (".xlsx", ".xlsm"):
            sheets = _extract_xlsx(file_path)
        elif suffix == ".xls":
            sheets = _extract_xls(file_path)
        elif suffix == ".csv":
            sheets = _extract_csv(file_path)
        else:
            return {"success": False, "error": f"Unsupported format: {suffix}"}
    except RuntimeError as exc:
        return {"success": False, "error": str(exc)}
    except Exception as exc:
        log.error("Extraction failed: %s", exc)
        return {"success": False, "error": f"Extraction error: {exc}"}

    # ── Build Markdown ──────────────────────────────────────────────────────
    md_parts = [f"# {name}\n"]
    sheet_summaries = []
    total_tables = 0

    for sheet in sheets:
        sname = sheet["name"]
        grid  = sheet["grid"]
        rows  = len(grid)
        cols  = len(grid[0]) if grid else 0
        empty = rows == 0

        md_parts.append(f"\n---\n\n## Sheet: {sname}\n")
        if not empty:
            md_parts.append(_grid_to_markdown(grid, sname))
            total_tables += 1
        else:
            md_parts.append(f"*Sheet `{sname}` is empty.*")

        sheet_summaries.append({
            "name": sname,
            "rows": rows,
            "cols": cols,
            "empty": empty,
            **sheet.get("meta", {}),
        })

    full_md = "\n".join(md_parts)
    md_path = out_dir / f"{name}.md"
    md_path.write_text(full_md, encoding="utf-8")

    # ── Build semantic JSON ─────────────────────────────────────────────────
    semantic = {
        "document": file_path.name,
        "schema_version": "1.0",
        "total_sheets": len(sheets),
        "total_tables": total_tables,
        "sheets": [
            {
                "name": s["name"],
                "rows": len(s["grid"]),
                "cols": len(s["grid"][0]) if s["grid"] else 0,
                "empty": len(s["grid"]) == 0,
                "meta": s.get("meta", {}),
                "data": s["grid"],          # full data for downstream use
            }
            for s in sheets
        ],
    }
    sem_path = out_dir / f"{name}.semantic.json"
    sem_path.write_text(json.dumps(semantic, indent=2, cls=_SafeEncoder), encoding="utf-8")

    log.info("Done: %s (sheets=%d, tables=%d)", file_path.name, len(sheets), total_tables)

    return {
        "success": True,
        "document": file_path.name,
        "sheets": sheet_summaries,
        "total_tables": total_tables,
        "markdown": full_md,
        "markdown_path": str(md_path),
        "semantic_json_path": str(sem_path),
        "error": None,
    }


def _run_pipeline(job_id: str, file_path: Path) -> None:
    _jobs[job_id]["status"] = "processing"
    try:
        result = _process_excel(file_path)
        _jobs[job_id].update({
            "status": "done" if result["success"] else "error",
            "result": result,
        })
    except Exception as exc:
        log.exception("Excel pipeline crashed for job %s", job_id)
        _jobs[job_id].update({
            "status": "error",
            "result": {"success": False, "error": str(exc)},
        })
    finally:
        try:
            file_path.unlink(missing_ok=True)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# API endpoints
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/upload", response_model=UploadResponse)
async def upload_excel(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """
    Upload a .xlsx, .xlsm, .xls, or .csv file for extraction.
    Returns a job_id to poll with GET /excel/status/{job_id}.
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"Only {', '.join(sorted(ALLOWED_EXT))} accepted. Got: '{suffix}'",
        )

    job_id = str(uuid.uuid4())
    tmp_path = STAGING_DIR / f"{job_id}{suffix}"

    try:
        with tmp_path.open("wb") as fh:
            shutil.copyfileobj(file.file, fh)
    finally:
        await file.close()

    _jobs[job_id] = {"status": "queued", "result": None, "filename": file.filename}
    background_tasks.add_task(_executor.submit, _run_pipeline, job_id, tmp_path)

    return UploadResponse(
        job_id=job_id,
        status="queued",
        message=f"'{file.filename}' queued. Poll /excel/status/{job_id}",
    )


@router.get("/status/{job_id}", response_model=JobStatus)
async def get_status(job_id: str):
    """Poll the status of an extraction job."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    result = job.get("result") or {}
    md = result.get("markdown", "")

    raw_sheets = result.get("sheets", [])
    sheets = [SheetSummary(**s) for s in raw_sheets] if raw_sheets else None

    return JobStatus(
        job_id=job_id,
        status=job["status"],
        document=result.get("document") or job.get("filename"),
        sheets=sheets,
        total_tables=result.get("total_tables"),
        markdown_preview=md[:3000] if md else None,
        markdown_path=result.get("markdown_path"),
        semantic_json_path=result.get("semantic_json_path"),
        error=result.get("error"),
    )


@router.get("/jobs")
async def list_jobs():
    """List all jobs (job_id, status, filename, sheet count)."""
    return [
        {
            "job_id": jid,
            "status": j["status"],
            "filename": j.get("filename"),
            "sheets": len((j.get("result") or {}).get("sheets", [])),
        }
        for jid, j in _jobs.items()
    ]


@router.get("/download/{job_id}/markdown")
async def download_markdown(job_id: str):
    """Download the extracted Markdown file."""
    job = _jobs.get(job_id)
    if not job or job["status"] != "done":
        raise HTTPException(status_code=404, detail="Job not ready.")
    path = (job["result"] or {}).get("markdown_path")
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="Markdown file not found.")
    return FileResponse(path, media_type="text/markdown", filename=Path(path).name)


@router.get("/download/{job_id}/semantic")
async def download_semantic(job_id: str):
    """Download the structured semantic JSON (full sheet data + metadata)."""
    job = _jobs.get(job_id)
    if not job or job["status"] != "done":
        raise HTTPException(status_code=404, detail="Job not ready.")
    path = (job["result"] or {}).get("semantic_json_path")
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="Semantic JSON not found.")
    return FileResponse(path, media_type="application/json", filename=Path(path).name)


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str):
    """Remove a job record from memory."""
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found.")
    _jobs.pop(job_id)
    return {"deleted": job_id}
