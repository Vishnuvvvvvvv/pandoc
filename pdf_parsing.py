"""
pdf_parsing.py
──────────────
FastAPI router for PDF parsing with:
  - pdfplumber  → native text & table extraction (digitally-born PDFs)
  - Amazon Textract → OCR + table detection (scanned / image-based PDFs)
  - Pillow       → image extraction via pdf2image
  - AWS Bedrock Nova Lite → chart/image semantic description (optional)

Mount in any FastAPI app:
    from pdf_parsing import router as pdf_router
    app.include_router(pdf_router, prefix="/pdf", tags=["PDF Pipeline"])
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import shutil
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv
from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

load_dotenv()

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("pdf_pipeline")

# ── Config ─────────────────────────────────────────────────────────────────────
AWS_REGION            = os.getenv("AWS_REGION", "us-east-1")
AWS_ACCESS_KEY_ID     = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
BEDROCK_MODEL_ID      = os.getenv("BEDROCK_MODEL_ID", "amazon.nova-lite-v1:0")
USE_VLM               = os.getenv("USE_VLM", "false").lower() == "true"
USE_TEXTRACT          = os.getenv("USE_TEXTRACT", "false").lower() == "true"
OUTPUT_DIR            = Path(os.getenv("PDF_OUTPUT_DIR", "./uploads/pdf"))
STAGING_DIR           = OUTPUT_DIR / "_staging"
TEXTRACT_MAX_BYTES    = 5 * 1024 * 1024  # 5 MB per-page limit
MIN_CHART_AREA        = 40_000
MIN_OCR_TOKENS        = 2
MAX_OCR_TOKENS        = 80

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

class PageResult(BaseModel):
    page_num: int
    text: str
    tables: list[list[list[str | None]]]
    images_count: int
    extraction_method: str  # "native" | "textract" | "hybrid"

class JobStatus(BaseModel):
    job_id: str
    status: str
    document: Optional[str] = None
    total_pages: Optional[int] = None
    tables_total: Optional[int] = None
    images_total: Optional[int] = None
    markdown_preview: Optional[str] = None
    markdown_path: Optional[str] = None
    semantic_json_path: Optional[str] = None
    error: Optional[str] = None

# ── Router ─────────────────────────────────────────────────────────────────────
router = APIRouter()

# ══════════════════════════════════════════════════════════════════════════════
# AWS helpers
# ══════════════════════════════════════════════════════════════════════════════
def _aws_kwargs() -> dict:
    kw: dict[str, Any] = {"region_name": AWS_REGION}
    if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
        kw["aws_access_key_id"] = AWS_ACCESS_KEY_ID
        kw["aws_secret_access_key"] = AWS_SECRET_ACCESS_KEY
    return kw


def _img_to_bytes(img, max_bytes: int = TEXTRACT_MAX_BYTES) -> bytes:
    """Compress PIL image to bytes suitable for Textract (≤5 MB)."""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = buf.getvalue()
    if len(data) <= max_bytes:
        return data
    for q in (85, 70, 55, 40):
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=q)
        data = buf.getvalue()
        if len(data) <= max_bytes:
            return data
    return data


# ══════════════════════════════════════════════════════════════════════════════
# Textract OCR + Table extraction
# ══════════════════════════════════════════════════════════════════════════════
def _textract_analyze_page(image_bytes: bytes) -> dict[str, Any]:
    """
    Run Textract AnalyzeDocument (TABLES + FORMS features) on a single page image.
    Returns {"text": str, "tables": [[[cell,...],...]]}
    """
    if not USE_TEXTRACT:
        return {"text": "", "tables": []}
    try:
        client = boto3.client("textract", **_aws_kwargs())
        response = client.analyze_document(
            Document={"Bytes": image_bytes},
            FeatureTypes=["TABLES", "FORMS"],
        )
        blocks = response.get("Blocks", [])

        # ── Plain text ─────────────────────────────────────────────────────────
        lines = [b["Text"] for b in blocks if b.get("BlockType") == "LINE"]
        text = "\n".join(lines).strip()

        # ── Tables ─────────────────────────────────────────────────────────────
        block_map = {b["Id"]: b for b in blocks}
        tables: list[list[list[str | None]]] = []

        for block in blocks:
            if block.get("BlockType") != "TABLE":
                continue
            # Collect cells
            cells: dict[tuple[int, int], str] = {}
            max_row = max_col = 0
            for rel in block.get("Relationships", []):
                if rel["Type"] != "CHILD":
                    continue
                for cid in rel["Ids"]:
                    cell_b = block_map.get(cid, {})
                    if cell_b.get("BlockType") != "CELL":
                        continue
                    r = cell_b.get("RowIndex", 1) - 1
                    c = cell_b.get("ColumnIndex", 1) - 1
                    max_row = max(max_row, r)
                    max_col = max(max_col, c)
                    # Gather words
                    words = []
                    for wrel in cell_b.get("Relationships", []):
                        if wrel["Type"] == "CHILD":
                            for wid in wrel["Ids"]:
                                w = block_map.get(wid, {})
                                if w.get("BlockType") == "WORD":
                                    words.append(w.get("Text", ""))
                    cells[(r, c)] = " ".join(words)

            if not cells:
                continue
            grid = [
                [cells.get((r, c), "") for c in range(max_col + 1)]
                for r in range(max_row + 1)
            ]
            tables.append(grid)

        return {"text": text, "tables": tables}

    except Exception as exc:
        log.warning("Textract error: %s", exc)
        return {"text": "", "tables": []}


# ══════════════════════════════════════════════════════════════════════════════
# VLM chart description (Bedrock Nova Lite)
# ══════════════════════════════════════════════════════════════════════════════
_CHART_PROMPT = (
    "Analyze this image extracted from a PDF. "
    "Is it a chart, graph, diagram, or data table? "
    "Return ONLY valid JSON (no markdown fences):\n"
    '{"chart_type": "<bar|line|pie|table|diagram|not_a_chart>", '
    '"series": [{"label": "<str>", "value": "<str>"}], '
    '"summary": "<one sentence>"}\n'
    'If photo/logo/decorative: {"chart_type": "not_a_chart", "series": [], "summary": ""}.'
)
_NOT_EVALUATED = {"chart_type": "not_evaluated", "series": [], "summary": ""}


def _looks_like_chart(img, ocr_text: str) -> bool:
    area = img.width * img.height
    tokens = len(ocr_text.split())
    return area > MIN_CHART_AREA and MIN_OCR_TOKENS <= tokens <= MAX_OCR_TOKENS


def _describe_chart(img_bytes: bytes) -> dict[str, Any]:
    if not USE_VLM:
        return _NOT_EVALUATED.copy()
    try:
        client = boto3.client("bedrock-runtime", **_aws_kwargs())
        response = client.converse(
            modelId=BEDROCK_MODEL_ID,
            messages=[{"role": "user", "content": [
                {"image": {"format": "png", "source": {"bytes": img_bytes}}},
                {"text": _CHART_PROMPT},
            ]}],
            inferenceConfig={"maxTokens": 512, "temperature": 0.1},
        )
        raw = response["output"]["message"]["content"][0]["text"].strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as exc:
        log.warning("VLM error: %s", exc)
        return {"chart_type": "error", "series": [], "summary": str(exc)}


# ══════════════════════════════════════════════════════════════════════════════
# PDF-native extraction via pdfplumber
# ══════════════════════════════════════════════════════════════════════════════
def _is_scanned_page(text: str) -> bool:
    """Heuristic: if a page yields <30 chars of native text it's likely scanned."""
    return len(text.strip()) < 30


def _pdfplumber_tables(page) -> list[list[list[str | None]]]:
    try:
        raw = page.extract_tables()
        return raw if raw else []
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════════════════════
# Core pipeline
# ══════════════════════════════════════════════════════════════════════════════
class _SafeEncoder(json.JSONEncoder):
    def default(self, obj):
        try:
            return super().default(obj)
        except TypeError:
            return str(obj)


def _table_to_markdown(table: list[list[str | None]]) -> str:
    if not table:
        return ""
    rows = []
    for i, row in enumerate(table):
        cells = [str(c or "").replace("|", "\\|").replace("\n", " ") for c in row]
        rows.append("| " + " | ".join(cells) + " |")
        if i == 0:
            rows.append("| " + " | ".join(["---"] * len(cells)) + " |")
    return "\n".join(rows)


def _process_pdf(pdf_path: Path) -> dict[str, Any]:
    try:
        import pdfplumber
    except ImportError:
        return {"success": False, "error": "pdfplumber not installed. Run: uv add pdfplumber"}

    try:
        from pdf2image import convert_from_path
        has_pdf2image = True
    except ImportError:
        has_pdf2image = False
        log.warning("pdf2image not installed — image extraction disabled")

    name = pdf_path.stem
    out_dir = OUTPUT_DIR / name
    out_dir.mkdir(parents=True, exist_ok=True)
    img_dir = out_dir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)

    log.info("Processing PDF: %s", pdf_path.name)

    md_parts: list[str] = [f"# {name}\n"]
    all_tables: list[dict] = []
    all_images: list[dict] = []
    page_results: list[dict] = []
    total_tables = 0

    # Render all pages to images upfront (needed for Textract + image save)
    page_images: list[Any] = []
    if has_pdf2image:
        try:
            page_images = convert_from_path(str(pdf_path), dpi=150)
        except Exception as exc:
            log.warning("pdf2image render failed: %s", exc)

    with pdfplumber.open(str(pdf_path)) as pdf:
        total_pages = len(pdf.pages)

        for page_num, page in enumerate(pdf.pages, start=1):
            log.info("  Page %d/%d", page_num, total_pages)
            md_parts.append(f"\n---\n## Page {page_num}\n")

            # ── Native text extraction ─────────────────────────────────────────
            native_text = (page.extract_text() or "").strip()
            native_tables = _pdfplumber_tables(page)
            is_scanned = _is_scanned_page(native_text)
            method = "native"

            # ── Textract fallback for scanned pages ───────────────────────────
            textract_text = ""
            textract_tables: list[list[list[str | None]]] = []
            pil_img = page_images[page_num - 1] if page_images and page_num - 1 < len(page_images) else None

            if is_scanned and pil_img and USE_TEXTRACT:
                log.info("    → Scanned page, running Textract OCR")
                img_bytes = _img_to_bytes(pil_img)
                tx_result = _textract_analyze_page(img_bytes)
                textract_text = tx_result["text"]
                textract_tables = tx_result["tables"]
                method = "textract"

            # Merge: prefer Textract if native came up empty
            final_text = textract_text if (is_scanned and textract_text) else native_text
            final_tables = textract_tables if (is_scanned and textract_tables) else native_tables

            # ── If neither native nor textract found text but Textract is off ─
            if is_scanned and not USE_TEXTRACT and pil_img:
                method = "scanned_no_ocr"

            # ── Hybrid: use native text + Textract tables if tables missing ──
            if not is_scanned and not native_tables and pil_img and USE_TEXTRACT:
                tx_result = _textract_analyze_page(_img_to_bytes(pil_img))
                if tx_result["tables"]:
                    final_tables = tx_result["tables"]
                    method = "hybrid"

            # ── Write text to markdown ─────────────────────────────────────────
            if final_text:
                md_parts.append(final_text + "\n")
            elif method in ("scanned_no_ocr",):
                md_parts.append("*[Scanned page — enable USE_TEXTRACT=true for OCR]*\n")

            # ── Write tables to markdown ───────────────────────────────────────
            for t_idx, table in enumerate(final_tables):
                t_md = _table_to_markdown(table)
                if t_md:
                    md_parts.append(f"\n**Table {total_tables + t_idx + 1}:**\n{t_md}\n")
                all_tables.append({
                    "page": page_num,
                    "table_index": total_tables + t_idx,
                    "rows": len(table),
                    "cols": len(table[0]) if table else 0,
                    "data": table,
                })
            total_tables += len(final_tables)

            # ── Save page image & run VLM ──────────────────────────────────────
            page_img_count = 0
            if pil_img:
                img_path = img_dir / f"page_{page_num:03d}.png"
                try:
                    pil_img.save(str(img_path), format="PNG")
                    page_img_count = 1

                    # VLM analysis
                    img_bytes_for_vlm = _img_to_bytes(pil_img)
                    ocr_snippet = (final_text or textract_text)[:200]
                    semantic = (
                        _describe_chart(img_bytes_for_vlm)
                        if (USE_VLM and _looks_like_chart(pil_img, ocr_snippet))
                        else _NOT_EVALUATED.copy()
                    )
                    all_images.append({
                        "page": page_num,
                        "file": str(img_path),
                        "width": pil_img.width,
                        "height": pil_img.height,
                        "semantic": semantic,
                    })

                    ct = semantic.get("chart_type", "")
                    if ct and ct not in ("not_evaluated", "not_a_chart", "error"):
                        md_parts.append(
                            f"\n> **Chart detected** (`{ct}`): {semantic.get('summary', '')}\n"
                        )
                except Exception as exc:
                    log.warning("Image save error p%d: %s", page_num, exc)

            page_results.append({
                "page": page_num,
                "text_chars": len(final_text),
                "tables": len(final_tables),
                "images": page_img_count,
                "method": method,
            })

    # ── Write outputs ──────────────────────────────────────────────────────────
    full_md = "\n".join(md_parts)
    md_path = out_dir / f"{name}.md"
    md_path.write_text(full_md, encoding="utf-8")

    semantic_data = {
        "document": pdf_path.name,
        "schema_version": "1.0",
        "total_pages": total_pages,
        "pages": page_results,
        "tables": all_tables,
        "images": all_images,
    }
    sem_path = out_dir / f"{name}.semantic.json"
    sem_path.write_text(json.dumps(semantic_data, indent=2, cls=_SafeEncoder), encoding="utf-8")

    log.info("Done: %s (pages=%d, tables=%d, images=%d)",
             pdf_path.name, total_pages, total_tables, len(all_images))

    return {
        "success": True,
        "document": pdf_path.name,
        "total_pages": total_pages,
        "tables_count": total_tables,
        "images_count": len(all_images),
        "markdown": full_md,
        "markdown_path": str(md_path),
        "semantic_json_path": str(sem_path),
        "error": None,
    }


def _run_pipeline(job_id: str, pdf_path: Path) -> None:
    _jobs[job_id]["status"] = "processing"
    try:
        result = _process_pdf(pdf_path)
        _jobs[job_id].update({
            "status": "done" if result["success"] else "error",
            "result": result,
        })
    except Exception as exc:
        log.exception("PDF pipeline crashed for job %s", job_id)
        _jobs[job_id].update({
            "status": "error",
            "result": {"success": False, "error": str(exc)},
        })
    finally:
        try:
            pdf_path.unlink(missing_ok=True)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# API endpoints
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/ui", response_class=HTMLResponse, include_in_schema=False)
async def pdf_test_ui():
    """Serve the interactive PDF extraction test UI."""
    html_path = Path(__file__).parent / "pdf_ui.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h2>pdf_ui.html not found</h2>", status_code=404)


@router.post("/upload", response_model=UploadResponse)
async def upload_pdf(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix != ".pdf":
        raise HTTPException(status_code=400, detail=f"Only .pdf accepted. Got: '{suffix}'")

    job_id = str(uuid.uuid4())
    tmp_path = STAGING_DIR / f"{job_id}.pdf"
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
        message=f"'{file.filename}' queued. Poll /pdf/status/{job_id}",
    )


@router.get("/status/{job_id}", response_model=JobStatus)
async def get_status(job_id: str):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    result = job.get("result") or {}
    md = result.get("markdown", "")
    return JobStatus(
        job_id=job_id,
        status=job["status"],
        document=result.get("document") or job.get("filename"),
        total_pages=result.get("total_pages"),
        tables_total=result.get("tables_count"),
        images_total=result.get("images_count"),
        markdown_preview=md[:3000] if md else None,
        markdown_path=result.get("markdown_path"),
        semantic_json_path=result.get("semantic_json_path"),
        error=result.get("error"),
    )


@router.get("/jobs")
async def list_jobs():
    return [
        {
            "job_id": jid,
            "status": j["status"],
            "filename": j.get("filename"),
            "pages": (j.get("result") or {}).get("total_pages"),
        }
        for jid, j in _jobs.items()
    ]


@router.get("/download/{job_id}/markdown")
async def download_markdown(job_id: str):
    job = _jobs.get(job_id)
    if job is None or job["status"] != "done":
        raise HTTPException(status_code=404, detail="Job not ready.")
    path = job["result"].get("markdown_path")
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="Markdown file not found.")
    return FileResponse(path, media_type="text/markdown", filename=Path(path).name)


@router.get("/download/{job_id}/semantic")
async def download_semantic(job_id: str):
    job = _jobs.get(job_id)
    if job is None or job["status"] != "done":
        raise HTTPException(status_code=404, detail="Job not ready.")
    path = job["result"].get("semantic_json_path")
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="Semantic JSON not found.")
    return FileResponse(path, media_type="application/json", filename=Path(path).name)


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found.")
    _jobs.pop(job_id)
    return {"deleted": job_id}
