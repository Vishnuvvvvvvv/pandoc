# GitHub Copilot Integration Prompt — Product Atlas MD Editor
# Paste this entire prompt into Copilot Chat on your company laptop
# ═══════════════════════════════════════════════════════════════════



=======================================================================================================================

=======================================================================================================================

markdown+grid_tables-raw_html

say im editing a  file which is  already signed and verfiied, then the copy on ediiting is getting stored somehere right , and  as the edititng is in progress and not verified yet the final verifiied document prev generated is still there , and now, when after clciking the view md file, on the current version tab , when i click , i am seeing teh old version/ the final document veriifed previously, but then the currently editing document is not beign able to see in the current verison tab. only afetr i clcik the edit button and go inside , then only im abel to see the edited version document getting laoded.


"When a previously verified document is being re-edited (status is 'In Progress'), opening the document in View Mode shows the old 'Final' version under the Current Version tab. It completely hides the active draft. The user has to click 'Edit' to actually see the draft they are working on. The Current Version tab should always prioritize showing the active draft if one exists."




also only when we clcik the edit button , then onyl the signed and verified button is getting vivsible, shouldnt it be visible once we clcik on to the view md ? 



=======================================================================================================================

I am building a React + FastAPI project called "Product Atlas" that browses SharePoint files via Microsoft Graph API and extracts Markdown from PDF/DOCX/XLSX. The extraction pipeline is already working. 

I need your help to implement the next phase: a secure, 3-stage Document Lifecycle Management pipeline.

## THE ARCHITECTURE & BUSINESS LOGIC
Here is how the flow must work after extraction is complete. Please keep this 3-stage lifecycle in mind for all code you generate:

**1. The "Raw" Stage (Immutable)**
* After extraction, the initial Markdown is saved to a `raw/` folder in SharePoint.
* Files in `raw/` are locked/read-only. They represent the exact baseline output of the AI/parser for auditing purposes.

**2. The "Draft" Stage (Editing)**
* A user logs into the UI, sees the document is "Not Verified", and clicks "Edit".
* The backend copies the file from `raw/` into a `drafts/` folder in SharePoint.
* The user edits the markdown in the React frontend. As they type, it auto-saves directly to the file in the `drafts/` folder.

**3. The "Final" Stage (Verified & Signed Off)**
* Once the user finishes fixing the markdown, they click "Verify & Sign Off".
* The backend copies the perfected draft into a `final/` folder in SharePoint.
* The draft is then deleted to clean up space.
* The database records exactly who verified it and when, updating the status to 'verified'.
* Downstream LLM/RAG applications will consume exclusively from this `final/` folder.

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
SHAREPOINT_SITE_ID = "YOUR_SITE_ID"          # from env
DRIVE_ID           = "YOUR_DRIVE_ID"          # from env
OUTPUTS_FOLDER     = "Atlas Outputs"          # root output folder in SharePoint

def sp_read_file(sp_path: str) -> str:
    """ Replace with: GET /sites/{site_id}/drives/{drive_id}/root:/{sp_path}:/content """
    return Path(sp_path).read_text(encoding="utf-8")

def sp_write_file(sp_path: str, content: str):
    """ Replace with: PUT /sites/{site_id}/drives/{drive_id}/root:/{sp_path}:/content """
    Path(sp_path).parent.mkdir(parents=True, exist_ok=True)
    Path(sp_path).write_text(content, encoding="utf-8")

def sp_copy_file(src_sp_path: str, dst_sp_path: str):
    """ Replace with: POST /drives/{drive_id}/root:/{src_path}:/copy """
    content = Path(src_sp_path).read_text(encoding="utf-8")
    sp_write_file(dst_sp_path, content)

def sp_delete_file(sp_path: str):
    """ Replace with: DELETE /drives/{drive_id}/root:/{sp_path}: """
    p = Path(sp_path)
    if p.exists(): p.unlink()

# SharePoint paths for each folder (adjust to match your tenant structure)
def raw_sp_path(doc_id: str)   -> str: return f"{OUTPUTS_FOLDER}/raw/{doc_id}.md"
def draft_sp_path(doc_id: str) -> str: return f"{OUTPUTS_FOLDER}/drafts/{doc_id}.md"
def final_sp_path(doc_id: str) -> str: return f"{OUTPUTS_FOLDER}/final/{doc_id}.md"
# ─────────────────────────────────────────────────────────────────────────────

def now_iso(): return datetime.now(timezone.utc).isoformat()

# Wire this to your existing DB session dependency
def get_db(): raise NotImplementedError("Wire to your DB session")

class StartEditBody(BaseModel): user: str = "editor@company.com"
class SaveDraftBody(BaseModel): content: str; user: str = "editor@company.com"
class DiscardBody(BaseModel): user: str = "editor@company.com"
class VerifyBody(BaseModel): user: str = "approver@company.com"; notes: Optional[str] = ""

def _get_proc(doc_id, db) -> dict:
    row = db.execute("SELECT * FROM md_processing WHERE document_id = %s", (doc_id,)).fetchone()
    return dict(row) if row else {}

def _ensure_proc(doc_id, db):
    if not _get_proc(doc_id, db):
        db.execute("INSERT INTO md_processing (id, document_id) VALUES (%s, %s)", (str(uuid.uuid4()), doc_id))

@router.get("/documents/{doc_id}/verification")
def get_verification_info(doc_id: str, db=Depends(get_db)):
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
    proc = _get_proc(doc_id, db)
    if version == "raw": path = proc.get("raw_md_path") or raw_sp_path(doc_id)
    elif version == "draft": path = proc.get("draft_md_path")
    else: path = proc.get("draft_md_path") or proc.get("final_md_path") or raw_sp_path(doc_id)
    
    if not path: raise HTTPException(404, "No MD path found")
    try: return {"content": sp_read_file(path), "version": version}
    except Exception: raise HTTPException(404, "MD file not found in SharePoint")

@router.post("/documents/{doc_id}/edit/start")
def start_edit(doc_id: str, body: StartEditBody, db=Depends(get_db)):
    _ensure_proc(doc_id, db)
    proc = _get_proc(doc_id, db)
    dst  = draft_sp_path(doc_id)
    if proc.get("draft_md_path"):
        source = "resumed"
    else:
        src = proc.get("final_md_path") or proc.get("raw_md_path") or raw_sp_path(doc_id)
        sp_copy_file(src, dst)
        source = "new"
    db.execute("""UPDATE md_processing SET draft_md_path=%s, edited_by=%s, edit_started_at=COALESCE(edit_started_at,%s) WHERE document_id=%s""", (dst, body.user, now_iso(), doc_id))
    db.execute("UPDATE documents SET verification_status='in_progress' WHERE id=%s", (doc_id,))
    db.commit()
    return {"status": "ok", "source": source}

@router.put("/documents/{doc_id}/edit/save")
def save_draft(doc_id: str, body: SaveDraftBody, db=Depends(get_db)):
    proc = _get_proc(doc_id, db)
    path = proc.get("draft_md_path") or draft_sp_path(doc_id)
    sp_write_file(path, body.content)
    db.execute("UPDATE md_processing SET last_saved_at=%s, edited_by=%s WHERE document_id=%s", (now_iso(), body.user, doc_id))
    db.commit()
    return {"status": "saved"}

@router.post("/documents/{doc_id}/edit/discard")
def discard_draft(doc_id: str, body: DiscardBody, db=Depends(get_db)):
    proc = _get_proc(doc_id, db)
    dp   = proc.get("draft_md_path")
    if dp: sp_delete_file(dp)
    db.execute("UPDATE md_processing SET draft_md_path=NULL, edit_started_at=NULL WHERE document_id=%s", (doc_id,))
    db.execute("UPDATE documents SET verification_status='not_verified' WHERE id=%s", (doc_id,))
    db.commit()
    return {"status": "discarded"}

@router.post("/documents/{doc_id}/verify")
def verify_document(doc_id: str, body: VerifyBody, db=Depends(get_db)):
    proc = _get_proc(doc_id, db)
    dp   = proc.get("draft_md_path")
    if not dp: raise HTTPException(400, "No active draft to verify")
    fp  = final_sp_path(doc_id)
    sp_copy_file(dp, fp)
    sp_delete_file(dp)
    ts  = now_iso()
    db.execute("""UPDATE md_processing SET final_md_path=%s, draft_md_path=NULL, edit_started_at=NULL, verification_notes=%s WHERE document_id=%s""", (fp, body.notes, doc_id))
    db.execute("""UPDATE documents SET verification_status='verified', verified_by=%s, verified_at=%s, sp_upload_date=%s WHERE id=%s""", (body.user, ts, ts, doc_id))
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
└── Atlas Outputs/          
    ├── raw/                ← system writes here after extraction (immutable)
    ├── drafts/             ← active edits (auto-cleaned after sign-off)
    └── final/              ← signed-off versions (permanent)
```

Update the `raw_md_path` in `md_processing` when extraction completes:
```python
# In your existing extraction pipeline, after MD is generated:
sp_path = f"Atlas Outputs/raw/{doc_id}.md"
upload_to_sharepoint(sp_path, markdown_content)   
db.execute("""INSERT INTO md_processing (id, document_id, raw_md_path)
              VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
           (str(uuid.uuid4()), doc_id, sp_path))
db.commit()
```
