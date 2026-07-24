# DOCX → Markdown Pipeline

A self-contained **FastAPI** service that converts `.doc` / `.docx` files into structured Markdown using **Pandoc** as the sole parsing engine. AWS Textract handles image OCR; AWS Bedrock (Nova Lite) handles chart detection.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [File Inventory](#file-inventory)
3. [How the Pipeline Works](#how-the-pipeline-works)
4. [API Reference](#api-reference)
5. [Configuration](#configuration)
6. [Running Locally](#running-locally)
7. [Docker Deployment](#docker-deployment)
8. [Excel Pipeline](#excel-pipeline)
9. [Code Audit — What Is Necessary vs Unnecessary](#code-audit)

---

## Architecture Overview

```
Client
  │
  │  POST /pandoc/upload  (multipart .doc/.docx)
  ▼
pandoc_app.py          ← FastAPI entry-point (CORS, health check, mounts router)
  │
  └─► pandoc_pipeline_router.py   ← All business logic lives here
        │
        ├─ _convert_doc_to_docx()     .doc  → .docx via doc2docx / LibreOffice
        ├─ _preprocess_list_nesting() DOCX XML patch for nested list styles
        ├─ pypandoc.convert_file()    DOCX  → GFM Markdown
        ├─ pypandoc.convert_file()    DOCX  → Pandoc JSON AST (DOM)
        ├─ zipfile extraction         DOCX word/media/* → images/
        ├─ process_extracted_images() OCR + chart detection per image
        └─ render_markdown()          Assembles final .md with image/chart sections
```

---

## File Inventory

| File | Role | Keep? |
|------|------|-------|
| `pandoc_app.py` | FastAPI app — CORS, health endpoint, mounts the router | Required |
| `pandoc_pipeline_router.py` | All pipeline logic: upload, job management, Pandoc conversion, OCR, VLM | Required |
| `docx_pipeline_router.py` | Docling-based pipeline (original, heavier engine) | Optional — only if you need Docling |
| `markitdown_pipeline_router.py` | MarkItDown-based pipeline alternative | Optional — only if you need MarkItDown |
| `test_router_app.py` | Minimal FastAPI app used for ad-hoc router testing | Dev-only |
| `Dockerfile` | Production container definition | Required for Docker |
| `pyproject.toml` | Python project / dependency declaration | Required |
| `.env` / `.env-example` | Environment variable overrides | Required (.env-example to commit, .env to gitignore) |
| `run.ps1` | PowerShell helper to start the server locally | Dev convenience |
| `steps.txt` | Ad-hoc notes / build steps | Delete or move to docs |
| `uv.lock` | Dependency lockfile (uv) | Required for reproducibility |

---

## How the Pipeline Works

### Step 0 — File Upload (`upload_document`)
- Accepts `multipart/form-data` with a `.doc` or `.docx` file.
- Validates the extension; rejects anything else with HTTP 400.
- Saves the raw bytes to `uploads/_staging/<job_id>.<ext>`.
- If the file is `.doc`, immediately converts it to `.docx` via `_convert_doc_to_docx()`.
- Creates a job record in the in-memory `_jobs` dict (`status: "queued"`).
- Submits `_run_pipeline()` to a `ThreadPoolExecutor` via FastAPI `BackgroundTasks`.
- Returns the `job_id` so the client can poll for status.

### Step 1 — `.doc` → `.docx` Conversion (`_convert_doc_to_docx`)
- Tries `doc2docx` first (Windows COM automation — requires Microsoft Word installed).
- Falls back to `soffice --headless` (LibreOffice) if `doc2docx` fails.
- Returns `None` if both fail → HTTP 422 is raised in the upload handler.

### Step 2 — XML Pre-processing (`_preprocess_list_nesting`)
Pandoc cannot detect nesting depth from Word's built-in `ListBullet2`, `ListBullet3`, `ListNumber2`... style names alone. This pre-processor fixes that **before Pandoc sees the file**:

1. Reads `word/styles.xml` to discover the real `numId` values for the bullet and number list families.
2. Patches `word/numbering.xml` to ensure `w:lvl` entries 1–4 exist for those `numId`s.
3. Rewrites each paragraph's `w:numPr` so the `w:ilvl` matches the style suffix number.
4. Writes the patched bytes to a temp `_fixed_<filename>.docx` file.
5. Returns the original path unchanged if no list-style paragraphs were found (no temp file created).

### Step 3 — Pandoc Markdown Extraction
```python
pypandoc.convert_file(fixed_docx, 'gfm', extra_args=['--wrap=none'])
```
- Output format: **GitHub Flavored Markdown**.
- `--wrap=none` prevents Pandoc from hard-wrapping long lines.
- Three regex post-processing passes clean up Pandoc artifacts:
  - Removes injected `<!-- -->` comments between loose list items.
  - Compresses `>\n\n<` in HTML table blocks to `>\n<`.
  - Unescapes `\$` → `$` (Pandoc escapes dollar signs for LaTeX safety).

### Step 4 — Pandoc JSON AST Extraction
```python
pypandoc.convert_file(docx_path, 'json')
```
Produces the full Pandoc internal document model as JSON. Traversed by `extract_tables_from_ast()` to pull out raw table nodes with colspans/rowspans intact.

### Step 5 — Image Extraction
All files under `word/media/` in the DOCX ZIP are extracted into `uploads/<docname>/images/`.

### Step 6 — OCR + Chart Detection (`process_extracted_images`)
For each image file:
1. **OCR** (`run_ocr`) — sends the image to Amazon Textract `detect_document_text`. Falls back to empty string on error.
2. **Chart heuristic** (`looks_like_chart`) — image area > 40,000 px² AND OCR word count between 2 and 80.
3. **VLM description** (`describe_chart`) — sends the image to AWS Bedrock Nova Lite with a strict JSON-schema prompt to classify chart type, extract data series, and produce a one-sentence summary. Only runs if the chart heuristic passes.

### Step 7 — Output Assembly
- `uploads/<docname>/<docname>.md` — Full Markdown with embedded image/chart sections appended.
- `uploads/<docname>/<docname>.semantic.json` — Structured JSON: tables (raw AST nodes) + images (OCR + VLM results).
- `uploads/<docname>/<docname>.dom.json` — Complete Pandoc AST for downstream programmatic use.

---

## API Reference

All routes are prefixed with `/pandoc`.

### `POST /pandoc/upload`
Upload a document for processing.

**Request:** `multipart/form-data`, field name `file`, `.doc` or `.docx`.

**Response:**
```json
{ "job_id": "uuid", "status": "queued", "message": "'file.docx' queued. Poll /status/<job_id>" }
```

### `GET /pandoc/status/{job_id}`
Poll job progress.

**Response statuses:** `queued` | `processing` | `done` | `error`

```json
{
  "job_id": "uuid",
  "status": "done",
  "document": "report.docx",
  "tables_count": 3,
  "images_count": 5,
  "markdown_preview": "# Report Title\n...",
  "markdown_path": "/app/uploads/report/report.md",
  "semantic_json_path": "/app/uploads/report/report.semantic.json",
  "dom_json_path": "/app/uploads/report/report.dom.json",
  "error": null
}
```

> **Note:** `markdown_preview` is always truncated to 2000 characters.

### `GET /pandoc/download/{job_id}/markdown`
Download the `.md` file. Returns `text/markdown`.

### `GET /pandoc/download/{job_id}/semantic`
Download the `.semantic.json` file. Returns `application/json`.

### `GET /health`
Liveness check. Returns `{"status": "ok", "engine": "pandoc"}`.

---

## Configuration

All settings are read from environment variables (or a `.env` file via `python-dotenv`).

| Variable | Default | Description |
|----------|---------|-------------|
| `AWS_REGION` | `us-east-1` | AWS region for Textract and Bedrock |
| `AWS_ACCESS_KEY_ID` | *(empty)* | Explicit AWS credentials (optional if using an IAM role) |
| `AWS_SECRET_ACCESS_KEY` | *(empty)* | Explicit AWS credentials |
| `BEDROCK_MODEL_ID` | `amazon.nova-lite-v1:0` | Bedrock model for chart description |
| `USE_VLM` | `true` | Set `false` to skip Bedrock chart detection |
| `USE_TEXTRACT` | `true` | Set `false` to skip OCR entirely |
| `DOCX_OUTPUT_DIR` | `./uploads` | Directory for all output files |

Copy `.env-example` to `.env` and fill in your values. **Never commit `.env`.**

---

## Running Locally

```bash
# Install dependencies (uv recommended)
uv sync

# Start server
uvicorn pandoc_app:app --host 0.0.0.0 --port 8001 --reload
```

Pandoc must be installed and on `PATH`.
- Ubuntu/Debian: `apt install pandoc`
- Windows: download from [pandoc.org](https://pandoc.org/installing.html)

---

## Docker Deployment

```bash
# Build
docker build -t docx-pipeline .

# Run (mount uploads volume for persistence)
docker run -d \
  --name docx-pipeline \
  -p 8001:8001 \
  -v $(pwd)/uploads:/app/uploads \
  --env-file .env \
  docx-pipeline
```

---

## Excel Pipeline

`excel_parsing.py` is a **self-contained FastAPI router** that extracts `.xlsx`, `.xlsm`, `.xls`, and `.csv` files into GFM Markdown tables and a structured semantic JSON.

### Reusing in Another Project

Only **one file** needs to be copied:

```
excel_parsing.py  →  your-other-project/excel_parsing.py
```

**1. Mount it in your `main.py` / `app.py`:**
```python
from excel_parsing import router as excel_router
app.include_router(excel_router, prefix="/excel", tags=["Excel Pipeline"])
```

**2. Install dependencies:**
```bash
uv add openpyxl xlrd
# or: pip install openpyxl xlrd
```

**3. Optional `.env` settings:**
```env
EXCEL_OUTPUT_DIR=./uploads/excel   # where outputs are saved
EXCEL_MAX_ROWS=5000                # safety cap per sheet
EXCEL_MAX_COLS=200
```

| What | Required? |
|------|-----------|
| `excel_parsing.py` | ✅ Only file needed |
| `openpyxl` | ✅ For `.xlsx` / `.xlsm` |
| `xlrd` | ✅ For legacy `.xls` |
| `python-dotenv` | ✅ Already in project |
| Any other router files | ❌ Not needed |

### API Endpoints

All routes are prefixed with `/excel`.

| Method | Route | Description |
|--------|-------|-------------|
| `POST` | `/excel/upload` | Upload `.xlsx`, `.xlsm`, `.xls`, or `.csv` |
| `GET` | `/excel/status/{job_id}` | Poll job — returns sheet summaries + markdown preview |
| `GET` | `/excel/jobs` | List all jobs |
| `GET` | `/excel/download/{job_id}/markdown` | Download `.md` file |
| `GET` | `/excel/download/{job_id}/semantic` | Download full `.semantic.json` (all sheet data) |
| `DELETE` | `/excel/jobs/{job_id}` | Remove job record from memory |

### How Extraction Works — Key Code Snippets

Three functions work together to faithfully capture the entire sheet, including complex multi-row headers:

**1. Merged cell expansion** — fills a merged header region with the top-left value so no cell appears blank:
```python
for merge_range in ws.merged_cells.ranges:
    top_left_cell = ws.cell(merge_range.min_row, merge_range.min_col)
    val = _cell_str(top_left_cell.value)
    for row in range(merge_range.min_row, merge_range.max_row + 1):
        for col in range(merge_range.min_col, merge_range.max_col + 1):
            merge_map[(row, col)] = val   # same value stamped into every merged cell
```
This is why a header like "Mixte (various variants)" spanning 2 rows appears in both rows instead of only the first.

**2. Raw row-by-row read** — every row, every cell, no skipping or interpretation:
```python
for row in ws.iter_rows(max_row=MAX_ROWS_PER_SHEET, max_col=MAX_COLS_PER_SHEET):
    cells = []
    for cell in row:
        val = merge_map.get((cell.row, cell.column), _cell_str(cell.value))
        cells.append(val)
    grid.append(cells)   # every row lands in the grid exactly as-is
```
No logic here decides "is this a header or data?" — everything is collected faithfully.

**3. Grid → Markdown** — `grid[0]` becomes the GFM header separator; all other rows become data rows:
```python
def _grid_to_markdown(grid, sheet_name):
    lines = [_row_md(grid[0])]                    # row 0 = markdown header
    lines.append("| --- | --- | ... |")            # GFM separator
    for row in grid[1:]:                           # rows 1-N = data
        lines.append(_row_md(row))
    return "\n".join(lines)
```

For a document with a multi-row header block (e.g., an insurance policy Excel):
```
grid[0] → "Blue Plan | Mixte... | Double Norwich..."  → markdown header
grid[1] → "MB000 | MB001-9 | MB012..."               → data row (sub-header)
grid[2] → "30 | 669 | 3..."                          → data row (counts)
grid[3] → "general answers | yes | yes..."           → data row (actual data)
```
All rows are preserved — nothing is interpreted or dropped.

### Known Edge Case — Blank First Row

If a sheet's **first row is empty** (e.g., a metadata/title sheet with content starting 5 rows down), `grid[0]` will be a row of empty cells, so the markdown header will appear blank:

```markdown
|  |  |  |           ← blank "header" from empty row 0
| --- | --- | --- |
|  |  |  |           ← more blank rows
| Abbreviation | Question Type |   ← real header, now a data row
| MC | Multiple Choice |
```

This is a known limitation of the no-heuristic approach. If you need to handle files where data always starts at a known row offset, the `EXCEL_MAX_ROWS` env var won't help — you would need a `?skip_rows=N` parameter (not yet implemented).

---

## Code Audit


### Necessary — Core Logic

| Component | Why it's needed |
|-----------|----------------|
| `_preprocess_list_nesting()` | Pandoc cannot infer nesting from Word `ListBullet2`/`ListNumber2` style names — this XML patch is the only reliable fix |
| `_img_to_bytes_for_textract()` | Textract has a 5 MB hard limit; iterative JPEG compression is required to stay under it |
| `_SafeEncoder` | Pandoc AST nodes can contain non-serializable types; a fallback JSON encoder prevents a crash on `json.dumps` |
| `looks_like_chart()` heuristic | Guards against unnecessary Bedrock API calls for photos/logos that clearly aren't charts |
| `ThreadPoolExecutor` + `BackgroundTasks` | Pandoc is CPU-bound and synchronous; offloading it prevents blocking the async event loop |

### Bugs Fixed

| Bug | File | Fix Applied |
|-----|------|------------|
| `//cors exten's` — invalid Python syntax (SyntaxError crash at startup) | `pandoc_app.py` | Replaced with `#` comment |
| `import re` inside the `_process_document` hot path | `pandoc_pipeline_router.py` | Moved to top-level imports |
| `import time as _time` — imported but never used | `pandoc_pipeline_router.py` | Removed |
| `STAGING_DIR.mkdir()` called again inside `_convert_doc_to_docx` — directory already created at module startup | `pandoc_pipeline_router.py` | Removed duplicate call |

### Unnecessary / Should Be Changed

| Item | Problem | Recommendation |
|------|---------|---------------|
| `BotoCoreError, ClientError` imported but unused | All AWS errors are swallowed by bare `except Exception` | Either use targeted `except (BotoCoreError, ClientError)` clauses or remove the import |
| Duplicated download endpoint guard logic | `download_markdown` and `download_semantic` repeat identical `_jobs.get / status check / path check` logic | Extract a `_get_done_job_path(job_id, result_key)` helper |
| `markdown_preview` hardcoded to 2000 chars | No way for clients to control truncation length | Accept an optional `?preview_len=N` query param |
| Missing download endpoint for DOM JSON | `dom_json_path` is returned in status but no download route exists for it | Add `GET /download/{job_id}/dom` |

### Not Suitable for Production As-Is

| Concern | Detail |
|---------|--------|
| **No authentication** | The upload endpoint accepts files from anyone — add an API key header or OAuth2 |
| **No file size limit** | A 1 GB DOCX will be streamed into memory — add `UploadFile` size checking or a reverse-proxy body limit |
| **No job TTL / cleanup** | `_jobs` grows unboundedly; extracted images and output files are never deleted — add a TTL sweep |
| **Single-node only** | `_jobs` is an in-process dict — multiple uvicorn workers or replicas will not share state; use Redis or a DB |
| **CORS wildcard** | `allow_origins=["*"]` exposes the API to any browser origin — restrict to your frontend domain in production |
| **No structured error codes** | All errors return generic HTTP 404/422 with plain text — use a consistent error body schema for clients |
