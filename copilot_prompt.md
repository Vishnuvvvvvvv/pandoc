# GitHub Copilot Integration Prompt — Product Atlas MD Editor
# Paste this entire prompt into Copilot Chat on your company laptop
# ═══════════════════════════════════════════════════════════════════

I am building a React + FastAPI project called "Product Atlas" that browses
SharePoint files via Microsoft Graph API and extracts Markdown from PDF/DOCX/XLSX.
The extraction pipeline is already working. I need to add the MD editing and
verification (sign-off) workflow described below.

---

## PART 1 — DATABASE MIGRATION

Run this SQL against our PostgreSQL database (add to Alembic migration or run directly):

```sql
-- Add to existing documents table
ALTER TABLE documents ADD COLUMN IF NOT EXISTS verification_status TEXT DEFAULT 'not_verified';
ALTER TABLE documents ADD COLUMN IF NOT EXISTS verified_by    TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS verified_at    TIMESTAMPTZ;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS sp_upload_date TIMESTAMPTZ;

-- New table for MD lifecycle paths and edit session tracking
CREATE TABLE IF NOT EXISTS md_processing (
    id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id        TEXT        NOT NULL REFERENCES documents(id),
    raw_md_path        TEXT,       -- SharePoint path: Atlas Outputs/raw/{doc_id}.md
    draft_md_path      TEXT,       -- SharePoint path: Atlas Outputs/drafts/{doc_id}.md
    final_md_path      TEXT,       -- SharePoint path: Atlas Outputs/final/{doc_id}.md
    edit_started_at    TIMESTAMPTZ,
    last_saved_at      TIMESTAMPTZ,
    edited_by          TEXT,
    verification_notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_md_processing_doc ON md_processing(document_id);
```

---

## PART 2 — BACKEND ENDPOINTS (FastAPI router to add)

Create a new file `md_editor_router.py` and add these endpoints.
Wire the three SharePoint helper functions to our existing Graph API client.

```python
"""
md_editor_router.py — add to main FastAPI app:
    from md_editor_router import router as editor_router
    app.include_router(editor_router, prefix="/api", tags=["MD Editor"])
"""
import uuid, shutil
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

router = APIRouter()

# ── WIRE THESE TO YOUR EXISTING GRAPH API CLIENT ─────────────────────────────
# Replace each function body with your real SharePoint Graph API calls.
# Your existing sharepoint.py likely already has download/upload helpers.

SHAREPOINT_SITE_ID = "YOUR_SITE_ID"          # from env
DRIVE_ID           = "YOUR_DRIVE_ID"          # from env
OUTPUTS_FOLDER     = "Atlas Outputs"          # root output folder in SharePoint

def sp_read_file(sp_path: str) -> str:
    """
    Download file content from SharePoint as text.
    sp_path is a relative path inside the drive, e.g. "Atlas Outputs/raw/abc.md"

    Replace with your Graph API call:
        GET /sites/{site_id}/drives/{drive_id}/root:/{sp_path}:/content
    """
    # MOCK (local file) — replace with:
    # response = graph_client.get(f"/drives/{DRIVE_ID}/root:/{sp_path}:/content")
    # return response.text
    return Path(sp_path).read_text(encoding="utf-8")

def sp_write_file(sp_path: str, content: str):
    """
    Upload/overwrite a file in SharePoint.
    sp_path is relative path, e.g. "Atlas Outputs/drafts/abc.md"

    Replace with your Graph API call:
        PUT /sites/{site_id}/drives/{drive_id}/root:/{sp_path}:/content
        body = content.encode("utf-8")
        headers = {"Content-Type": "text/markdown"}
    """
    # MOCK — replace with:
    # graph_client.put(
    #     f"/drives/{DRIVE_ID}/root:/{sp_path}:/content",
    #     data=content.encode("utf-8"),
    #     headers={"Content-Type": "text/plain"}
    # )
    Path(sp_path).parent.mkdir(parents=True, exist_ok=True)
    Path(sp_path).write_text(content, encoding="utf-8")

def sp_copy_file(src_sp_path: str, dst_sp_path: str):
    """
    Copy a file within SharePoint (draft → final on sign-off).

    Replace with:
        1. Read src content via sp_read_file
        2. Write to dst via sp_write_file
    Or use Graph API copy endpoint:
        POST /drives/{drive_id}/root:/{src_path}:/copy
    """
    # MOCK — replace with:
    # content = sp_read_file(src_sp_path)
    # sp_write_file(dst_sp_path, content)
    content = Path(src_sp_path).read_text(encoding="utf-8")
    sp_write_file(dst_sp_path, content)

def sp_delete_file(sp_path: str):
    """
    Delete a file from SharePoint (used to clean up drafts after sign-off).

    Replace with:
        DELETE /drives/{drive_id}/root:/{sp_path}:
    """
    # MOCK — replace with:
    # graph_client.delete(f"/drives/{DRIVE_ID}/root:/{sp_path}:")
    p = Path(sp_path)
    if p.exists():
        p.unlink()

# SharePoint paths for each folder (adjust to match your tenant structure)
def raw_sp_path(doc_id: str)   -> str: return f"{OUTPUTS_FOLDER}/raw/{doc_id}.md"
def draft_sp_path(doc_id: str) -> str: return f"{OUTPUTS_FOLDER}/drafts/{doc_id}.md"
def final_sp_path(doc_id: str) -> str: return f"{OUTPUTS_FOLDER}/final/{doc_id}.md"
# ─────────────────────────────────────────────────────────────────────────────

def now_iso(): return datetime.now(timezone.utc).isoformat()

# Wire this to your existing DB session dependency
def get_db(): raise NotImplementedError("Wire to your DB session")

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

def _get_proc(doc_id, db) -> dict:
    row = db.execute(
        "SELECT * FROM md_processing WHERE document_id = %s", (doc_id,)
    ).fetchone()
    return dict(row) if row else {}

def _ensure_proc(doc_id, db):
    if not _get_proc(doc_id, db):
        db.execute(
            "INSERT INTO md_processing (id, document_id) VALUES (%s, %s)",
            (str(uuid.uuid4()), doc_id)
        )

@router.get("/documents/{doc_id}/verification")
def get_verification_info(doc_id: str, db=Depends(get_db)):
    """Returns data shown in the verification badge popup."""
    doc  = db.execute("SELECT * FROM documents WHERE id=%s", (doc_id,)).fetchone()
    proc = _get_proc(doc_id, db)
    return {
        "verification_status": doc["verification_status"],
        "verified_by":         doc.get("verified_by"),
        "verified_at":         doc.get("verified_at"),
        "sp_upload_date":      doc.get("sp_upload_date"),
        "edited_by":           proc.get("edited_by"),
        "last_saved_at":       proc.get("last_saved_at"),
        "notes":               proc.get("verification_notes"),
    }

@router.get("/documents/{doc_id}/content")
def get_md_content(doc_id: str, version: str = "current", db=Depends(get_db)):
    """version: 'raw' | 'draft' | 'current' (draft > final > raw)"""
    proc = _get_proc(doc_id, db)
    if version == "raw":
        path = proc.get("raw_md_path") or raw_sp_path(doc_id)
    elif version == "draft":
        path = proc.get("draft_md_path")
    else:
        path = proc.get("draft_md_path") or proc.get("final_md_path") or raw_sp_path(doc_id)
    if not path:
        raise HTTPException(404, "No MD path found")
    try:
        return {"content": sp_read_file(path), "version": version}
    except Exception:
        raise HTTPException(404, "MD file not found in SharePoint")

@router.post("/documents/{doc_id}/edit/start")
def start_edit(doc_id: str, body: StartEditBody, db=Depends(get_db)):
    """Start or resume editing. Copies raw → drafts/ on first edit."""
    _ensure_proc(doc_id, db)
    proc = _get_proc(doc_id, db)
    dst  = draft_sp_path(doc_id)
    if proc.get("draft_md_path"):
        source = "resumed"
    else:
        src = proc.get("raw_md_path") or raw_sp_path(doc_id)
        sp_copy_file(src, dst)
        source = "new"
    db.execute("""UPDATE md_processing SET draft_md_path=%s, edited_by=%s,
        edit_started_at=COALESCE(edit_started_at,%s) WHERE document_id=%s""",
        (dst, body.user, now_iso(), doc_id))
    db.execute("UPDATE documents SET verification_status='in_progress' WHERE id=%s", (doc_id,))
    db.commit()
    return {"status": "ok", "source": source}

@router.put("/documents/{doc_id}/edit/save")
def save_draft(doc_id: str, body: SaveDraftBody, db=Depends(get_db)):
    """Overwrite the draft in SharePoint with current editor content."""
    proc = _get_proc(doc_id, db)
    path = proc.get("draft_md_path") or draft_sp_path(doc_id)
    sp_write_file(path, body.content)   # ← writes to SharePoint drafts/
    db.execute("UPDATE md_processing SET last_saved_at=%s, edited_by=%s WHERE document_id=%s",
               (now_iso(), body.user, doc_id))
    db.commit()
    return {"status": "saved"}

@router.post("/documents/{doc_id}/edit/discard")
def discard_draft(doc_id: str, body: DiscardBody, db=Depends(get_db)):
    """Delete the draft from SharePoint and reset status."""
    proc = _get_proc(doc_id, db)
    dp   = proc.get("draft_md_path")
    if dp:
        sp_delete_file(dp)              # ← deletes from SharePoint drafts/
    db.execute("UPDATE md_processing SET draft_md_path=NULL, edit_started_at=NULL WHERE document_id=%s", (doc_id,))
    db.execute("UPDATE documents SET verification_status='not_verified' WHERE id=%s", (doc_id,))
    db.commit()
    return {"status": "discarded"}

@router.post("/documents/{doc_id}/verify")
def verify_document(doc_id: str, body: VerifyBody, db=Depends(get_db)):
    """
    Sign-off: copies draft → final/ in SharePoint (single file, overwritten each time).
    Sets verification_status='verified', records verified_by + sp_upload_date.
    """
    proc = _get_proc(doc_id, db)
    dp   = proc.get("draft_md_path")
    if not dp:
        raise HTTPException(400, "No active draft to verify")
    fp  = final_sp_path(doc_id)
    sp_copy_file(dp, fp)                # ← copies draft → final in SharePoint
    sp_delete_file(dp)                  # ← removes draft from SharePoint
    ts  = now_iso()
    db.execute("""UPDATE md_processing SET final_md_path=%s, draft_md_path=NULL,
        edit_started_at=NULL, verification_notes=%s WHERE document_id=%s""",
        (fp, body.notes, doc_id))
    db.execute("""UPDATE documents SET verification_status='verified',
        verified_by=%s, verified_at=%s, sp_upload_date=%s WHERE id=%s""",
        (body.user, ts, ts, doc_id))
    db.commit()
    return {"status": "verified", "verified_by": body.user, "sp_upload_date": ts}
```

---

## PART 3 — REACT COMPONENT (MDEditorPage.tsx)

Copy the file `MDEditorPage.tsx` (provided separately) into `src/components/`.

Install required packages if not already present:
```bash
npm install react-markdown remark-gfm
```

Set `API_BASE` at the top of the file to your backend URL (e.g. `"/api"` or `"http://localhost:8000/api"`).

Use it wherever the user clicks "View .md" in the document list:
```tsx
import MDEditorPage from './components/MDEditorPage';

// In your document list component state:
const [selectedDocId, setSelectedDocId] = useState<string | null>(null);

// Trigger when View .md is clicked:
<button onClick={() => setSelectedDocId(doc.id)}>View .md</button>

// Render the editor:
{selectedDocId && (
  <MDEditorPage
    docId={selectedDocId}
    currentUser={currentUserEmail}   // pass logged-in user UPN from Entra ID auth context
    onClose={() => { setSelectedDocId(null); refreshDocumentList(); }}
  />
)}
```

### What MDEditorPage provides (no further changes needed):
- "Original MD" tab → view raw extracted content (read-only)
- "Current Version" tab → view latest version (draft/final/raw)
- "✏️ Edit" / "▶️ Resume Edit" button → always visible in view mode
- Resizable split pane (drag the divider between editor and preview)
- "👁 Preview Only" toggle → full-width rendered markdown, no editor
- "🔍 View Original" button in edit mode → opens comparison overlay
- "💾 Save" → saves draft to SharePoint via PUT /edit/save
- "✅ Verify & Sign Off" → opens modal with doc info + notes, calls POST /verify
- Verified badge (✅) is clickable → popup shows verified_by, verified_at, sp_upload_date, notes
- Auto-saves every 60 seconds

### Columns to add to your existing document list table:
```tsx
// Verification Status (replaces or supplements existing status column)
<td>
  {doc.verification_status === 'verified'    && <span style={{color:'#4ade80'}}>✅ Verified</span>}
  {doc.verification_status === 'in_progress' && <span style={{color:'#60a5fa'}}>✏️ In Progress</span>}
  {doc.verification_status === 'not_verified'&& <span style={{color:'#64748b'}}>⬜ Not Verified</span>}
</td>

// Signed Off By
<td>{doc.verified_by || '—'}</td>
```

---

## PART 4 — SHAREPOINT FOLDER STRUCTURE TO CREATE

Ask your SharePoint admin (or create via Graph API) this folder structure
inside your existing SharePoint site:

```
Sites/YourSite/
└── Atlas Outputs/          ← create this folder
    ├── raw/                ← system writes here after extraction (immutable)
    ├── drafts/             ← active edits (auto-cleaned after sign-off)
    └── final/              ← signed-off versions (permanent)
```

Update the `raw_md_path` in `md_processing` when extraction completes:
```python
# In your existing extraction pipeline, after MD is generated:
sp_path = f"Atlas Outputs/raw/{doc_id}.md"
upload_to_sharepoint(sp_path, markdown_content)   # your existing Graph upload helper
db.execute("""INSERT INTO md_processing (id, document_id, raw_md_path)
              VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
           (str(uuid.uuid4()), doc_id, sp_path))
db.commit()
```

---

## SUMMARY OF WHAT TO DO

1. Run the SQL migration
2. Create `md_editor_router.py` with the code above; wire `get_db()`, `sp_read_file()`, `sp_write_file()`, `sp_copy_file()`, `sp_delete_file()` to your existing Graph API helpers
3. In `main.py`: `app.include_router(editor_router, prefix="/api")`
4. Copy `MDEditorPage.tsx` to `src/components/`; set `API_BASE`; render it on "View .md" click
5. Create the `Atlas Outputs/raw/drafts/final/` folder structure in SharePoint
6. After extraction, store the raw MD path in `md_processing.raw_md_path`
