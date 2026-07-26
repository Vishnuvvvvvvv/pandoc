-- ════════════════════════════════════════════════════════════
-- Migration: Add MD editing + verification to existing schema
-- Run this against your PostgreSQL database
-- ════════════════════════════════════════════════════════════

-- 1. New columns on your EXISTING documents table
ALTER TABLE documents ADD COLUMN IF NOT EXISTS verification_status TEXT DEFAULT 'not_verified';
-- values: 'not_verified' | 'in_progress' | 'verified'
ALTER TABLE documents ADD COLUMN IF NOT EXISTS verified_by    TEXT;          -- Entra ID UPN
ALTER TABLE documents ADD COLUMN IF NOT EXISTS verified_at    TIMESTAMPTZ;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS sp_upload_date TIMESTAMPTZ;   -- when final MD hit SharePoint

-- 2. New table: all MD lifecycle paths + edit metadata
CREATE TABLE IF NOT EXISTS md_processing (
    id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id        TEXT        NOT NULL REFERENCES documents(id),
    -- SharePoint paths (use local paths for dev/mock)
    raw_md_path        TEXT,       -- Atlas Outputs/raw/{doc_id}.md        (immutable)
    draft_md_path      TEXT,       -- Atlas Outputs/drafts/{doc_id}.md     (active edit)
    final_md_path      TEXT,       -- Atlas Outputs/final/{doc_id}.md      (overwritten on each verify)
    -- Edit session tracking
    edit_started_at    TIMESTAMPTZ,
    last_saved_at      TIMESTAMPTZ,
    edited_by          TEXT,       -- Entra ID UPN of current/last editor
    verification_notes TEXT        -- notes entered at sign-off
);

-- 3. Index for fast lookup by document
CREATE INDEX IF NOT EXISTS idx_md_processing_doc ON md_processing(document_id);