"""
md_editor_router.py
────────────────────
Modular FastAPI router for the MD editor / verification workflow.
Copy this file into your existing FastAPI project and include it:

    from md_editor_router import router as editor_router
    app.include_router(editor_router, prefix="/api", tags=["MD Editor"])

Prerequisites already in your project:
  - DB connection helper (adapt get_db() below to match yours)
  - SharePoint Graph client (adapt sp_read / sp_write / sp_copy below)
  - Documents table with the new columns from migration_md_editor.sql
"""
import shutil, uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

router = APIRouter()

# ── Adapt these to match YOUR project ────────────────────────────────────────
# Replace with your actual DB session dependency
def get_db():
    """Yield your SQLAlchemy/asyncpg session here."""
    raise NotImplementedError("Wire up your DB session")

# Replace with your actual SharePoint Graph API helpers
def sp_read(sp_path: str) -> str:
    """Read file content from SharePoint. Return string."""
    return Path(sp_path).read_text(encoding="utf-8")  # mock: local file

def sp_write(sp_path: str, content: str):
    """Write content to SharePoint."""
    Path(sp_path).parent.mkdir(parents=True, exist_ok=True)
    Path(sp_path).write_text(content, encoding="utf-8")  # mock: local file

def sp_copy(src: str, dst: str):
    """Copy a file within SharePoint."""
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dst)  # mock: local file

# Local mock SharePoint folder (swap for real SP paths in production)
MOCK_BASE  = Path(__file__).parent / "mock_sharepoint"
RAW_DIR    = MOCK_BASE / "raw"
DRAFTS_DIR = MOCK_BASE / "drafts"
FINAL_DIR  = MOCK_BASE / "final"
for _d in [RAW_DIR, DRAFTS_DIR, FINAL_DIR]:
    _d.mkdir(parents=True, exist_ok=True)
# ─────────────────────────────────────────────────────────────────────────────

def now_iso(): return datetime.now(timezone.utc).isoformat()

# ── Pydantic models ───────────────────────────────────────────────────────────
class StartEditBody(BaseModel):
    user: str = "editor@company.com"

class SaveDraftBody(BaseModel):
    content: str
    user: str = "editor@company.com"

class DiscardBody(BaseModel):
    user: str = "editor@company.com"

class VerifyBody(BaseModel):
    user: str = "approver@company.com"
    notes: Optional[str] = ""

# ── DB helpers (replace with your ORM calls) ──────────────────────────────────
def _get_doc(doc_id: str, db) -> dict:
    row = db.execute(
        "SELECT * FROM documents WHERE id = %s", (doc_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, f"Document {doc_id} not found")
    return dict(row)

def _get_proc(doc_id: str, db) -> dict:
    row = db.execute(
        "SELECT * FROM md_processing WHERE document_id = %s", (doc_id,)
    ).fetchone()
    return dict(row) if row else {}

def _ensure_proc(doc_id: str, db):
    """Create md_processing row if it doesn't exist yet."""
    if not _get_proc(doc_id, db):
        db.execute(
            "INSERT INTO md_processing (id, document_id) VALUES (%s, %s)",
            (str(uuid.uuid4()), doc_id)
        )

# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/documents/{doc_id}/verification")
def get_verification_info(doc_id: str, db=Depends(get_db)):
    """Data for the verification popup badge click."""
    doc  = _get_doc(doc_id, db)
    proc = _get_proc(doc_id, db)
    return {
        "verification_status": doc.get("verification_status", "not_verified"),
        "verified_by":         doc.get("verified_by"),
        "verified_at":         doc.get("verified_at"),
        "sp_upload_date":      doc.get("sp_upload_date"),
        "edited_by":           proc.get("edited_by"),
        "last_saved_at":       proc.get("last_saved_at"),
        "notes":               proc.get("verification_notes"),
    }


@router.get("/documents/{doc_id}/content")
def get_md_content(doc_id: str, version: str = "current", db=Depends(get_db)):
    """
    version:
      'raw'     → original extracted MD (immutable)
      'draft'   → active edit draft
      'current' → draft > final > raw (best available)
    """
    proc = _get_proc(doc_id, db)
    if version == "raw":
        path = proc.get("raw_md_path")
    elif version == "draft":
        path = proc.get("draft_md_path")
    else:  # current
        path = proc.get("draft_md_path") or proc.get("final_md_path") or proc.get("raw_md_path")

    if not path:
        raise HTTPException(404, f"No MD path found for version='{version}'")
    try:
        content = sp_read(path)
        return {"content": content, "version": version, "path": path}
    except FileNotFoundError:
        raise HTTPException(404, f"MD file not found at: {path}")


@router.post("/documents/{doc_id}/edit/start")
def start_edit(doc_id: str, body: StartEditBody, db=Depends(get_db)):
    """
    Start or resume an edit session.
    Copies raw (or final) → drafts/ if no draft exists.
    Always returns OK so the frontend can load the draft content.
    """
    _ensure_proc(doc_id, db)
    proc       = _get_proc(doc_id, db)
    draft_path = str(DRAFTS_DIR / f"{doc_id}.md")

    if proc.get("draft_md_path") and Path(draft_path).exists():
        source = "resumed"
    else:
        src = proc.get("final_md_path") or proc.get("raw_md_path")
        if not src:
            raise HTTPException(400, "No source MD to start editing from")
        sp_copy(src, draft_path)
        source = "new"

    db.execute("""
        UPDATE md_processing
        SET draft_md_path    = %s,
            edited_by        = %s,
            edit_started_at  = COALESCE(edit_started_at, %s)
        WHERE document_id = %s
    """, (draft_path, body.user, now_iso(), doc_id))

    db.execute("""
        UPDATE documents SET verification_status = 'in_progress' WHERE id = %s
    """, (doc_id,))
    db.commit()
    return {"status": "ok", "source": source}


@router.put("/documents/{doc_id}/edit/save")
def save_draft(doc_id: str, body: SaveDraftBody, db=Depends(get_db)):
    """Overwrite the draft with current editor content."""
    proc = _get_proc(doc_id, db)
    path = proc.get("draft_md_path") or str(DRAFTS_DIR / f"{doc_id}.md")
    sp_write(path, body.content)
    db.execute("""
        UPDATE md_processing
        SET last_saved_at = %s, edited_by = %s, draft_md_path = %s
        WHERE document_id = %s
    """, (now_iso(), body.user, path, doc_id))
    db.commit()
    return {"status": "saved", "saved_at": now_iso()}


@router.post("/documents/{doc_id}/edit/discard")
def discard_draft(doc_id: str, body: DiscardBody, db=Depends(get_db)):
    """Delete the draft and reset verification_status to not_verified."""
    proc = _get_proc(doc_id, db)
    dp   = proc.get("draft_md_path")
    if dp and Path(dp).exists():
        Path(dp).unlink()
    db.execute("""
        UPDATE md_processing
        SET draft_md_path = NULL, edit_started_at = NULL
        WHERE document_id = %s
    """, (doc_id,))
    db.execute("""
        UPDATE documents SET verification_status = 'not_verified' WHERE id = %s
    """, (doc_id,))
    db.commit()
    return {"status": "discarded"}


@router.post("/documents/{doc_id}/verify")
def verify_document(doc_id: str, body: VerifyBody, db=Depends(get_db)):
    """
    Sign off: copies draft → final/ (single file, overwritten each time),
    deletes draft, marks document as verified.
    """
    proc = _get_proc(doc_id, db)
    dp   = proc.get("draft_md_path")
    if not dp or not Path(dp).exists():
        raise HTTPException(400, "No active draft to verify")

    final_path = str(FINAL_DIR / f"{doc_id}.md")   # overwritten each verify
    sp_copy(dp, final_path)
    Path(dp).unlink()

    ts = now_iso()
    db.execute("""
        UPDATE md_processing
        SET final_md_path      = %s,
            draft_md_path      = NULL,
            edit_started_at    = NULL,
            verification_notes = %s
        WHERE document_id = %s
    """, (final_path, body.notes, doc_id))
    db.execute("""
        UPDATE documents
        SET verification_status = 'verified',
            verified_by         = %s,
            verified_at         = %s,
            sp_upload_date      = %s
        WHERE id = %s
    """, (body.user, ts, ts, doc_id))
    db.commit()
    return {"status": "verified", "verified_by": body.user, "verified_at": ts}
