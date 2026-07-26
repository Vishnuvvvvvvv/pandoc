/**
 * MDEditorPage.tsx
 * ─────────────────
 * Modular, self-contained React component for viewing and editing
 * extracted Markdown documents with save + verify (sign-off) workflow.
 *
 * USAGE in your existing page/router:
 *   import MDEditorPage from './MDEditorPage';
 *   // When user clicks "View .md":
 *   <MDEditorPage docId={selectedDocId} currentUser="john@company.com" onClose={() => setDocId(null)} />
 *
 * DEPENDENCIES (install if not already present):
 *   npm install react-markdown remark-gfm
 *
 * API_BASE: set to your backend URL, e.g. "http://localhost:8000/api"
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

// ── Config ────────────────────────────────────────────────────────
const API_BASE = '/api'; // change to your backend base URL

// ── Types ─────────────────────────────────────────────────────────
interface DocDetail {
  id: string;
  original_filename: string;
  sharepoint_path: string;
  verification_status: 'not_verified' | 'in_progress' | 'verified';
  verified_by?: string;
  verified_at?: string;
  sp_upload_date?: string;
  processing?: {
    last_saved_at?: string;
    edited_by?: string;
    draft_md_path?: string;
    verification_notes?: string;
  };
}

interface VerificationInfo {
  verification_status: string;
  verified_by?: string;
  verified_at?: string;
  sp_upload_date?: string;
  edited_by?: string;
  last_saved_at?: string;
  notes?: string;
}

interface Props {
  docId: string;
  currentUser?: string;
  onClose?: () => void;
}

// ── Helpers ───────────────────────────────────────────────────────
function fmtDate(s?: string) {
  if (!s) return '—';
  try {
    return new Date(s).toLocaleString('en-GB', {
      day: '2-digit', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  } catch { return s; }
}

// ── Main Component ────────────────────────────────────────────────
export default function MDEditorPage({ docId, currentUser = 'editor@company.com', onClose }: Props) {
  const [doc, setDoc]               = useState<DocDetail | null>(null);
  const [content, setContent]       = useState('');
  const [tab, setTab]               = useState<'raw' | 'current'>('raw');
  const [mode, setMode]             = useState<'view' | 'edit'>('view');
  const [saveStatus, setSaveStatus] = useState('');
  const [loading, setLoading]       = useState(true);
  const [verPopup, setVerPopup]     = useState(false);
  const [verInfo, setVerInfo]       = useState<VerificationInfo | null>(null);
  const [verModal, setVerModal]     = useState(false);
  const [verNotes, setVerNotes]     = useState('');
  const [toast, setToast]           = useState('');
  // Original MD comparison modal (shown while in edit mode)
  const [origModal, setOrigModal]   = useState(false);
  const [origContent, setOrigContent] = useState('');
  // Split pane controls
  const [splitPct, setSplitPct]     = useState(50);     // editor width %
  const [previewOnly, setPreviewOnly] = useState(false); // hide editor, full preview
  const autoRef    = useRef<NodeJS.Timeout | null>(null);
  const isDragging = useRef(false);
  const splitRef   = useRef<HTMLDivElement>(null);

  // ── Drag-to-resize ─────────────────────────────────────────────────
  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!isDragging.current || !splitRef.current) return;
      const rect = splitRef.current.getBoundingClientRect();
      const pct  = ((e.clientX - rect.left) / rect.width) * 100;
      setSplitPct(Math.min(80, Math.max(20, pct))); // clamp 20–80%
    };
    const onUp = () => { isDragging.current = false; document.body.style.cursor = ''; document.body.style.userSelect = ''; };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup',   onUp);
    return () => { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp); };
  }, []);

  // ── Load document ─────────────────────────────────────────────
  const loadDoc = useCallback(async () => {
    setLoading(true);
    const res = await fetch(`${API_BASE}/documents/${docId}`);
    const d   = await res.json();
    setDoc(d);
    setLoading(false);

    // FIX: always start in view mode when (re)loading
    setMode('view');

    // Load appropriate content for current tab
    await loadContent(tab, d.verification_status);
  }, [docId]);

  useEffect(() => { loadDoc(); }, [loadDoc]);

  // ── Load MD content ───────────────────────────────────────────
  const loadContent = async (version: string, status?: string) => {
    try {
      const v = version === 'raw' ? 'raw' : 'current';
      const res = await fetch(`${API_BASE}/documents/${docId}/content?version=${v}`);
      if (!res.ok) { setContent(''); return; }
      const d = await res.json();
      setContent(d.content || '');
    } catch { setContent(''); }
  };

  // ── Toast ─────────────────────────────────────────────────────
  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(''), 2500);
  };

  // ── Show original MD comparison while editing ─────────────────
  const showOriginalComparison = async () => {
    try {
      const res = await fetch(`${API_BASE}/documents/${docId}/content?version=raw`);
      if (res.ok) {
        const d = await res.json();
        setOrigContent(d.content || 'No original content found.');
      } else {
        setOrigContent('Could not load original MD.');
      }
    } catch {
      setOrigContent('Error loading original MD.');
    }
    setOrigModal(true);
  };

  // ── Start / Resume edit ───────────────────────────────────────
  const startEdit = async () => {
    await fetch(`${API_BASE}/documents/${docId}/edit/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user: currentUser }),
    });
    // Load draft content into editor
    const res = await fetch(`${API_BASE}/documents/${docId}/content?version=draft`);
    if (res.ok) {
      const d = await res.json();
      setContent(d.content || '');
    }
    setMode('edit');
    setSaveStatus('Unsaved changes');
    // Auto-save every 60 seconds
    if (autoRef.current) clearInterval(autoRef.current);
    autoRef.current = setInterval(() => saveDraft(true), 60000);
    showToast('Edit session started');
  };

  // ── Save draft ────────────────────────────────────────────────
  const saveDraft = async (auto = false) => {
    await fetch(`${API_BASE}/documents/${docId}/edit/save`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, user: currentUser }),
    });
    setSaveStatus(`Saved ${new Date().toLocaleTimeString()}`);
    if (!auto) showToast('Draft saved 💾');
  };

  // ── Discard draft ─────────────────────────────────────────────
  const discardEdit = async () => {
    if (!window.confirm('Discard all changes?')) return;
    if (autoRef.current) clearInterval(autoRef.current);
    await fetch(`${API_BASE}/documents/${docId}/edit/discard`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user: currentUser }),
    });
    showToast('Changes discarded');
    await loadDoc(); // resets mode to 'view' via loadDoc
  };

  // ── Verify / Sign-off ─────────────────────────────────────────
  const confirmVerify = async () => {
    await saveDraft(true);
    const res = await fetch(`${API_BASE}/documents/${docId}/verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user: currentUser, notes: verNotes }),
    });
    if (autoRef.current) clearInterval(autoRef.current);
    setVerModal(false);
    showToast('Document Verified ✅');
    await loadDoc();
  };

  // ── Verification popup ────────────────────────────────────────
  const showVerDetails = async () => {
    const res = await fetch(`${API_BASE}/documents/${docId}/verification`);
    const d   = await res.json();
    setVerInfo(d);
    setVerPopup(true);
  };

  // ── Render ────────────────────────────────────────────────────
  if (loading) return <div style={s.loading}>Loading...</div>;
  if (!doc)    return <div style={s.loading}>Document not found.</div>;

  const isVerified   = doc.verification_status === 'verified';
  const isInProgress = doc.verification_status === 'in_progress';

  return (
    <div style={s.root}>
      {/* ── Header ── */}
      <div style={s.header}>
        {onClose && <button style={s.btnGhost} onClick={onClose}>← Back</button>}
        <span style={s.filename}>{doc.original_filename}</span>

        {/* Verification Status Badge */}
        {isVerified ? (
          <button style={s.badgeVerified} onClick={showVerDetails}>✅ Verified</button>
        ) : isInProgress ? (
          <span style={s.badgeProgress}>✏️ In Progress</span>
        ) : (
          <span style={s.badgeNot}>⬜ Not Verified</span>
        )}

        {/* Tabs */}
        <div style={s.tabs}>
          <button
            // In edit mode this is just an action button — never mark it active
            style={mode === 'edit' ? s.tab : (tab === 'raw' ? s.tabActive : s.tab)}
            title={mode === 'edit' ? 'Compare with original (opens panel)' : 'View original extracted MD'}
            onClick={() => {
              if (mode === 'edit') {
                // DON'T change tab state — just open the comparison overlay
                showOriginalComparison();
              } else {
                setTab('raw');
                loadContent('raw');
              }
            }}
          >
            {mode === 'edit' ? '🔍 View Original' : 'Original MD'}
          </button>
          <button
            // In edit mode 'Current Version' is always the active tab
            style={mode === 'edit' ? s.tabActive : (tab === 'current' ? s.tabActive : s.tab)}
            onClick={() => { setTab('current'); if (mode === 'view') loadContent('current'); }}
          >
            Current Version
          </button>
        </div>

        {/* Edit button — ALWAYS visible in view mode; shows "Resume Edit" if in_progress */}
        {mode === 'view' && (
          <button style={s.btnPrimary} onClick={startEdit}>
            {isInProgress ? '▶️ Resume Edit' : '✏️ Edit'}
          </button>
        )}
      </div>

      {/* ── Meta bar ── */}
      <div style={s.metaBar}>
        <span style={s.metaItem}>📁 <b>{doc.sharepoint_path || '—'}</b></span>
        <span style={s.metaItem}>💾 Last Saved: <b>{fmtDate(doc.processing?.last_saved_at)}</b></span>
        {isVerified && <span style={s.metaItem}>✅ Verified By: <b style={{color:'#4ade80'}}>{doc.verified_by}</b></span>}
      </div>

      {/* ── VIEW MODE ── */}
      {mode === 'view' && (
        <div style={s.preview}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
        </div>
      )}

      {/* ── EDIT MODE ── */}
      {mode === 'edit' && (
        <>
          <div style={s.editBar}>
            <span style={{flex:1, fontSize:13, color:'#60a5fa'}}>
              ✏️ Editing as <b>{currentUser}</b> · {saveStatus}
            </span>
            {/* Preview-only toggle */}
            <button
              style={{...s.btnGhost, color: previewOnly ? '#60a5fa' : '#94a3b8',
                      border: previewOnly ? '1px solid #2563eb' : '1px solid transparent'}}
              title={previewOnly ? 'Back to split view' : 'View preview only (full width)'}
              onClick={() => setPreviewOnly(v => !v)}
            >
              {previewOnly ? '↔ Split View' : '👁 Preview Only'}
            </button>
            <button style={s.btnGhost}   onClick={discardEdit}>Discard</button>
            <button style={s.btnPrimary} onClick={() => saveDraft()}>💾 Save</button>
            <button style={s.btnGreen}   onClick={() => setVerModal(true)}>✅ Verify & Sign Off</button>
          </div>
          {/* ── Preview-only mode: full width rendered MD ── */}
          {previewOnly ? (
            <div style={{...s.preview, minHeight:'70vh', overflow:'auto'}} className="md-content">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
            </div>
          ) : (
            /* ── Split pane with draggable divider ── */
            <div style={s.split} ref={splitRef}>
              {/* Editor pane */}
              <textarea
                style={{...s.editor, width: `${splitPct}%`, flex: 'none'}}
                value={content}
                onChange={e => { setContent(e.target.value); setSaveStatus('Unsaved changes'); }}
              />

              {/* Draggable divider */}
              <div
                style={{
                  width: 6, background: '#1e2a3a', cursor: 'col-resize',
                  flexShrink: 0, transition: 'background .15s',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                }}
                title="Drag to resize"
                onMouseDown={() => {
                  isDragging.current = true;
                  document.body.style.cursor = 'col-resize';
                  document.body.style.userSelect = 'none';
                }}
                onMouseEnter={e => (e.currentTarget.style.background = '#2563eb')}
                onMouseLeave={e => { if (!isDragging.current) e.currentTarget.style.background = '#1e2a3a'; }}
              >
                <div style={{width:2, height:40, background:'#334155', borderRadius:2}} />
              </div>

              {/* Preview pane */}
              <div style={{flex:1, overflow:'auto', padding:'24px 28px',
                           lineHeight:1.8, fontSize:15, minWidth:0}} className="md-content">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
              </div>
            </div>
          )}
        </>
      )}

      {/* ── Verification Popup ── */}
      {verPopup && verInfo && (
        <div style={s.overlay} onClick={() => setVerPopup(false)}>
          <div style={s.modal} onClick={e => e.stopPropagation()}>
            <h3 style={{marginBottom:16}}>🔍 Verification Details</h3>
            {[
              ['Verified By',       verInfo.verified_by],
              ['Verified At',       fmtDate(verInfo.verified_at)],
              ['SharePoint Upload', fmtDate(verInfo.sp_upload_date)],
              ['Last Edited By',    verInfo.edited_by],
              ['Notes',             verInfo.notes || '—'],
            ].map(([l,v]) => (
              <div key={l as string} style={s.modalRow}>
                <span style={{color:'#64748b'}}>{l}</span>
                <span style={{color:'#e2e8f0', fontWeight:500}}>{v || '—'}</span>
              </div>
            ))}
            <button style={{...s.btnGhost, marginTop:16}} onClick={() => setVerPopup(false)}>Close</button>
          </div>
        </div>
      )}

      {/* ── Sign Off Modal ── */}
      {verModal && (
        <div style={s.overlay} onClick={() => setVerModal(false)}>
          <div style={{...s.modal, width: 480}} onClick={e => e.stopPropagation()}>
            <h3 style={{marginBottom:18, fontSize:17}}>✅ Verify & Sign Off</h3>

            {/* Document summary */}
            <div style={{background:'#0d1117', borderRadius:8, padding:'14px 16px', marginBottom:16}}>
              {[
                ['Document',    doc.original_filename],
                ['Location',    doc.sharepoint_path || '—'],
                ['Verified By', currentUser],
              ].map(([l, v]) => (
                <div key={l} style={{display:'flex', justifyContent:'space-between', padding:'6px 0', borderBottom:'1px solid #1e2a3a', fontSize:13}}>
                  <span style={{color:'#64748b'}}>{l}</span>
                  <span style={{color:'#e2e8f0', fontWeight:500, maxWidth:'60%', textAlign:'right', wordBreak:'break-all'}}>{v}</span>
                </div>
              ))}
            </div>

            <label style={{display:'block', fontSize:12, color:'#64748b', marginBottom:6}}>Notes (optional)</label>
            <textarea
              style={s.notesInput}
              placeholder="Add sign-off notes..."
              value={verNotes}
              onChange={e => setVerNotes(e.target.value)}
              autoFocus
            />

            <p style={{fontSize:12, color:'#475569', marginBottom:16}}>
              ⚠️ The current draft will become the verified final MD. This action can be undone by re-editing.
            </p>

            <div style={{display:'flex', gap:10, justifyContent:'flex-end'}}>
              <button style={s.btnGhost} onClick={() => setVerModal(false)}>Cancel</button>
              <button style={s.btnGreen} onClick={confirmVerify}>✅ Confirm Sign Off</button>
            </div>
          </div>
        </div>
      )}

      {/* ── Original MD Comparison Modal (shown while editing) ── */}
      {origModal && (
        <div style={s.overlay} onClick={() => setOrigModal(false)}>
          <div
            style={{background:'#161b27', border:'1px solid #1e2a3a', borderRadius:14,
                    width:'80vw', maxWidth:900, maxHeight:'85vh', display:'flex', flexDirection:'column',
                    overflow:'hidden'}}
            onClick={e => e.stopPropagation()}
          >
            <div style={{display:'flex', alignItems:'center', padding:'14px 20px',
                         borderBottom:'1px solid #1e2a3a', gap:12}}>
              <h3 style={{flex:1, fontSize:15}}>🔍 Original Extracted MD — {doc.original_filename}</h3>
              <span style={{fontSize:12, color:'#64748b'}}>Read-only reference</span>
              <button style={s.btnGhost} onClick={() => setOrigModal(false)}>✕ Close</button>
            </div>
            <div style={{overflow:'auto', padding:'24px 28px', flex:1,
                         lineHeight:1.8, fontSize:14}}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{origContent}</ReactMarkdown>
            </div>
          </div>
        </div>
      )}

      {/* ── Toast ── */}
      {toast && (
        <div style={s.toast}>{toast}</div>
      )}
    </div>
  );
}

// ── Inline styles (adapt to your design system) ───────────────────
const s: Record<string, React.CSSProperties> = {
  root:         { background:'#0f1117', color:'#e2e8f0', minHeight:'100vh', fontFamily:'Segoe UI, sans-serif' },
  loading:      { padding:40, textAlign:'center', color:'#64748b' },
  header:       { display:'flex', alignItems:'center', gap:12, padding:'16px 24px', borderBottom:'1px solid #1e2a3a', flexWrap:'wrap' },
  filename:     { flex:1, fontSize:17, fontWeight:600, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' },
  tabs:         { display:'flex', gap:4, background:'#161b27', borderRadius:8, padding:4, border:'1px solid #1e2a3a' },
  tab:          { padding:'5px 14px', borderRadius:6, border:'none', background:'transparent', color:'#64748b', cursor:'pointer', fontSize:13 },
  tabActive:    { padding:'5px 14px', borderRadius:6, border:'none', background:'#2563eb', color:'#fff', cursor:'pointer', fontSize:13 },
  btnPrimary:   { padding:'6px 14px', borderRadius:7, border:'none', background:'#2563eb', color:'#fff', cursor:'pointer', fontWeight:600, fontSize:13 },
  btnGhost:     { padding:'6px 14px', borderRadius:7, border:'none', background:'#1e2a3a', color:'#94a3b8', cursor:'pointer', fontWeight:600, fontSize:13 },
  btnGreen:     { padding:'6px 14px', borderRadius:7, border:'none', background:'#065f46', color:'#34d399', cursor:'pointer', fontWeight:600, fontSize:13 },
  badgeVerified:{ padding:'5px 12px', borderRadius:99, border:'1px solid #16a34a44', background:'#14532d', color:'#4ade80', cursor:'pointer', fontWeight:600, fontSize:12 },
  badgeProgress:{ padding:'5px 12px', borderRadius:99, border:'1px solid #2563eb44', background:'#1e3a5f', color:'#60a5fa', fontWeight:600, fontSize:12 },
  badgeNot:     { padding:'5px 12px', borderRadius:99, border:'1px solid #334155', background:'#1e293b', color:'#64748b', fontWeight:600, fontSize:12 },
  metaBar:      { display:'flex', gap:24, padding:'10px 24px', background:'#161b27', borderBottom:'1px solid #1e2a3a', fontSize:13, flexWrap:'wrap' },
  metaItem:     { color:'#64748b' },
  preview:      { padding:'28px 36px', lineHeight:1.8, fontSize:15, background:'#161b27', minHeight:'60vh' },
  editBar:      { display:'flex', alignItems:'center', gap:12, padding:'10px 16px', background:'#1a2035', borderBottom:'1px solid #2563eb', flexWrap:'wrap' },
  split:        { display:'flex', height:'70vh' },
  editor:       { flex:1, background:'#0d1117', border:'none', borderRight:'1px solid #1e2a3a', padding:16, color:'#e2e8f0', fontFamily:'Courier New, monospace', fontSize:13, lineHeight:1.6, resize:'none', outline:'none' },
  overlay:      { position:'fixed', inset:0, background:'rgba(0,0,0,0.7)', display:'flex', alignItems:'center', justifyContent:'center', zIndex:200 },
  modal:        { background:'#161b27', border:'1px solid #1e2a3a', borderRadius:14, padding:28, width:420, maxWidth:'95vw' },
  modalRow:     { display:'flex', justifyContent:'space-between', padding:'10px 0', borderBottom:'1px solid #1e2a3a', fontSize:14 },
  notesInput:   { width:'100%', background:'#0d1117', border:'1px solid #1e2a3a', borderRadius:8, padding:10, color:'#e2e8f0', fontSize:13, height:80, resize:'none', outline:'none', marginBottom:16 },
  toast:        { position:'fixed', bottom:24, right:24, background:'#1e3a5f', color:'#60a5fa', border:'1px solid #2563eb', borderRadius:8, padding:'12px 20px', fontSize:13, zIndex:300 },
};
