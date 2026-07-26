"""
atlas_mvp.py — Product Atlas Phase 1 MVP
Two-table design: documents (core) + md_processing (lifecycle)
Single final MD per document, overwritten on each verification.
"""
import sqlite3, uuid, shutil
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Product Atlas MVP")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

BASE       = Path(__file__).parent
MOCK_SP    = BASE / "mock_sharepoint"
RAW_DIR    = MOCK_SP / "raw"
DRAFTS_DIR = MOCK_SP / "drafts"
FINAL_DIR  = MOCK_SP / "final"
DB_PATH    = BASE / "atlas_mvp.db"

for d in [RAW_DIR, DRAFTS_DIR, FINAL_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ── DB ────────────────────────────────────────────────────────────
def db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS documents (
        id                  TEXT PRIMARY KEY,
        original_filename   TEXT NOT NULL,
        sharepoint_path     TEXT,
        file_type           TEXT,
        uploaded_at         TEXT,
        status              TEXT DEFAULT 'not_processed',
        processed_at        TEXT,
        error_message       TEXT,
        -- Single verification column (replaces edit_status + version)
        verification_status TEXT DEFAULT 'not_verified',
        verified_by         TEXT,
        verified_at         TEXT,
        sp_upload_date      TEXT,
        -- SharePoint identifiers (kept for real integration later)
        item_id             TEXT,
        site_id             TEXT,
        drive_id            TEXT
    );

    CREATE TABLE IF NOT EXISTS md_processing (
        id               TEXT PRIMARY KEY,
        document_id      TEXT NOT NULL,
        raw_md_path      TEXT,
        draft_md_path    TEXT,
        final_md_path    TEXT,
        edit_started_at  TEXT,
        last_saved_at    TEXT,
        edited_by        TEXT,
        verification_notes TEXT,
        FOREIGN KEY(document_id) REFERENCES documents(id)
    );
    """)
    conn.commit()
    conn.close()

init_db()

def now(): return datetime.utcnow().isoformat()
def nid(): return str(uuid.uuid4())

def get_doc(doc_id):
    conn = db()
    row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    conn.close()
    if not row: raise HTTPException(404, "Document not found")
    return dict(row)

def get_proc(doc_id):
    conn = db()
    row = conn.execute("SELECT * FROM md_processing WHERE document_id=?", (doc_id,)).fetchone()
    conn.close()
    return dict(row) if row else {}

# ── Seed ──────────────────────────────────────────────────────────
SAMPLE_MD = """# {name}

## Background and Context

This document covers the policy details and value-for-money assessment
for the **{name}** proposition.

## Assessment Outcomes

| Area | Rating |
| --- | --- |
| Target Market | Fair |
| Commission Structure | Needs Improvement |
| Costs and Charges | Not Applicable |
| Customer Claims | Inconclusive |

## Overall Summary

The propositions covered by this review are unit-linked pension plans
designed to provide benefits at retirement and on death. They were sold
by Provident Mutual as Individual Plans and Group Schemes.

## Actions

| Action | Owner | Target Date |
| --- | --- | --- |
| Review pricing model | Product Team | Q3 2024 |
| Update customer comms | Comms Team | Q4 2024 |
"""

@app.post("/api/seed")
def seed():
    mock_docs = [
        ("230713 Funeral Plan.docx",  "OLAB/1M - Old Products/", "docx"),
        ("Acrobat Document.pdf",      "OLAB/1M - Old Products/", "pdf"),
        ("Aviva matrix.xlsx",         "OLAB/1M - Old Products/", "xlsx"),
        ("VFM037 Recalibrated.xlsx",  "Pensions/026 - SHP YP/",  "xlsx"),
        ("French Funeral Plan.pdf",   "OLAB/1M - Old Products/", "pdf"),
        ("Contract questions.docx",   "Investments/034-Multi/",   "docx"),
    ]
    created = []
    conn = db()
    try:
        for fname, sp_path, ftype in mock_docs:
            ex = conn.execute("SELECT id FROM documents WHERE original_filename=?", (fname,)).fetchone()
            if ex: continue
            doc_id  = nid()
            proc_id = nid()
            raw_path = RAW_DIR / f"{doc_id}.md"
            raw_path.write_text(SAMPLE_MD.format(name=fname.rsplit(".", 1)[0]), encoding="utf-8")
            conn.execute("""INSERT INTO documents
                (id,original_filename,sharepoint_path,file_type,uploaded_at,status,processed_at,verification_status)
                VALUES (?,?,?,?,?,?,?,?)""",
                (doc_id, fname, sp_path, ftype, now(), "extracted", now(), "not_verified"))
            conn.execute("""INSERT INTO md_processing
                (id,document_id,raw_md_path)
                VALUES (?,?,?)""",
                (proc_id, doc_id, str(raw_path)))
            created.append(fname)
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()
    return {"seeded": created, "total": len(created)}

# ── List documents ────────────────────────────────────────────────
@app.get("/api/documents")
def list_documents():
    conn = db()
    rows = conn.execute("""
        SELECT d.id, d.original_filename, d.sharepoint_path, d.file_type,
               d.status, d.processed_at,
               d.verification_status, d.verified_by, d.verified_at, d.sp_upload_date,
               p.edited_by, p.last_saved_at
        FROM documents d
        LEFT JOIN md_processing p ON p.document_id = d.id
        ORDER BY d.processed_at DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ── Document detail ───────────────────────────────────────────────
@app.get("/api/documents/{doc_id}")
def document_detail(doc_id: str):
    doc  = get_doc(doc_id)
    proc = get_proc(doc_id)
    return {**doc, "processing": proc}

# ── Verification popup data ───────────────────────────────────────
@app.get("/api/documents/{doc_id}/verification")
def verification_info(doc_id: str):
    doc  = get_doc(doc_id)
    proc = get_proc(doc_id)
    return {
        "verification_status": doc.get("verification_status"),
        "verified_by":         doc.get("verified_by"),
        "verified_at":         doc.get("verified_at"),
        "sp_upload_date":      doc.get("sp_upload_date"),
        "notes":               proc.get("verification_notes"),
        "edited_by":           proc.get("edited_by"),
        "last_saved_at":       proc.get("last_saved_at"),
    }

# ── Get MD content ────────────────────────────────────────────────
@app.get("/api/documents/{doc_id}/content")
def get_content(doc_id: str, version: str = "current"):
    proc = get_proc(doc_id)
    if version == "raw":
        path = proc.get("raw_md_path")
    elif version == "draft":
        path = proc.get("draft_md_path")
    else:  # current: draft → final → raw
        path = proc.get("draft_md_path") or proc.get("final_md_path") or proc.get("raw_md_path")
    if not path or not Path(path).exists():
        raise HTTPException(404, f"No MD for version={version}")
    return {"content": Path(path).read_text(encoding="utf-8"), "version": version}

# ── Start edit ────────────────────────────────────────────────────
@app.post("/api/documents/{doc_id}/edit/start")
def start_edit(doc_id: str, body: dict = None):
    if body is None: body = {}
    proc = get_proc(doc_id)
    user = body.get("user", "editor@company.com")
    draft_path = DRAFTS_DIR / f"{doc_id}.md"

    if proc.get("draft_md_path") and draft_path.exists():
        pass  # resume
    else:
        src = proc.get("final_md_path") or proc.get("raw_md_path")
        if not src or not Path(src).exists():
            raise HTTPException(400, "No source MD")
        shutil.copy(src, draft_path)

    conn = db()
    conn.execute("""UPDATE md_processing SET draft_md_path=?, edited_by=?,
        edit_started_at=COALESCE(edit_started_at,?) WHERE document_id=?""",
        (str(draft_path), user, now(), doc_id))
    conn.execute("UPDATE documents SET verification_status='in_progress' WHERE id=?", (doc_id,))
    conn.commit(); conn.close()
    return {"status": "ok"}

# ── Save draft ────────────────────────────────────────────────────
@app.put("/api/documents/{doc_id}/edit/save")
def save_draft(doc_id: str, body: dict = None):
    if body is None: body = {}
    proc    = get_proc(doc_id)
    content = body.get("content", "")
    user    = body.get("user", "editor@company.com")
    path    = Path(proc.get("draft_md_path") or str(DRAFTS_DIR / f"{doc_id}.md"))
    path.write_text(content, encoding="utf-8")
    conn = db()
    conn.execute("UPDATE md_processing SET last_saved_at=?,edited_by=? WHERE document_id=?",
                 (now(), user, doc_id))
    conn.commit(); conn.close()
    return {"status": "saved", "saved_at": now()}

# ── Discard ───────────────────────────────────────────────────────
@app.post("/api/documents/{doc_id}/edit/discard")
def discard_draft(doc_id: str, body: dict = None):
    if body is None: body = {}
    proc = get_proc(doc_id)
    dp   = proc.get("draft_md_path")
    if dp and Path(dp).exists(): Path(dp).unlink()
    conn = db()
    conn.execute("UPDATE md_processing SET draft_md_path=NULL,edit_started_at=NULL WHERE document_id=?", (doc_id,))
    conn.execute("UPDATE documents SET verification_status='not_verified' WHERE id=?", (doc_id,))
    conn.commit(); conn.close()
    return {"status": "discarded"}

# ── Verify (Sign Off) ─────────────────────────────────────────────
@app.post("/api/documents/{doc_id}/verify")
def verify_document(doc_id: str, body: dict = None):
    if body is None: body = {}
    proc  = get_proc(doc_id)
    user  = body.get("user", "approver@company.com")
    notes = body.get("notes", "")
    dp    = proc.get("draft_md_path")
    if not dp or not Path(dp).exists():
        raise HTTPException(400, "No active draft to verify")

    # Single final MD — overwrite every time (no versioning)
    final_path = FINAL_DIR / f"{doc_id}.md"
    shutil.copy(dp, final_path)
    Path(dp).unlink()

    ts = now()
    conn = db()
    conn.execute("""UPDATE md_processing SET
        final_md_path=?, draft_md_path=NULL,
        last_saved_at=?, verification_notes=?, edited_by=NULL
        WHERE document_id=?""",
        (str(final_path), ts, notes, doc_id))
    conn.execute("""UPDATE documents SET
        verification_status='verified', verified_by=?,
        verified_at=?, sp_upload_date=?
        WHERE id=?""",
        (user, ts, ts, doc_id))
    conn.commit(); conn.close()
    return {"status": "verified", "verified_by": user, "verified_at": ts}

# ── Serve UI ──────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def serve_ui():
    ui = Path(__file__).parent / "atlas_mvp.html"
    if ui.exists():
        return HTMLResponse(ui.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Place atlas_mvp.html next to atlas_mvp.py</h1>")
