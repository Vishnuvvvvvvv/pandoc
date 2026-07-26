import { useState, useEffect } from 'react'
import MDEditorPage from './MDEditorPage'
import './index.css'

const API = '/api'

interface Doc {
  id: string
  original_filename: string
  sharepoint_path: string
  file_type: string
  status: string
  processed_at: string
  verification_status: 'not_verified' | 'in_progress' | 'verified'
  verified_by?: string
  last_saved_at?: string
}

function fmtDate(s?: string) {
  if (!s) return '—'
  try { return new Date(s).toLocaleString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) }
  catch { return s }
}

function VerBadge({ status }: { status: string }) {
  const map: Record<string, { bg: string; color: string; label: string }> = {
    verified:    { bg: '#14532d', color: '#4ade80', label: '✅ Verified' },
    in_progress: { bg: '#1e3a5f', color: '#60a5fa', label: '✏️ In Progress' },
    not_verified:{ bg: '#1e293b', color: '#64748b', label: '⬜ Not Verified' },
  }
  const { bg, color, label } = map[status] || map.not_verified
  return (
    <span style={{ background: bg, color, padding: '4px 10px', borderRadius: 99, fontSize: 12, fontWeight: 600 }}>
      {label}
    </span>
  )
}

export default function App() {
  const [docs, setDocs]         = useState<Doc[]>([])
  const [loading, setLoading]   = useState(true)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [seeding, setSeeding]   = useState(false)
  const [page, setPage]         = useState<'browse' | 'history'>('browse')

  const loadDocs = async () => {
    setLoading(true)
    try {
      const res = await fetch(`${API}/documents`)
      setDocs(await res.json())
    } catch { setDocs([]) }
    setLoading(false)
  }

  const seed = async () => {
    setSeeding(true)
    await fetch(`${API}/seed`, { method: 'POST' })
    await loadDocs()
    setSeeding(false)
  }

  useEffect(() => { loadDocs() }, [])

  const stats = {
    total:      docs.length,
    extracted:  docs.filter(d => d.status === 'extracted').length,
    inProgress: docs.filter(d => d.verification_status === 'in_progress').length,
    verified:   docs.filter(d => d.verification_status === 'verified').length,
  }

  // ── Editor view ───────────────────────────────────────────────
  if (selectedId) {
    return (
      <MDEditorPage
        docId={selectedId}
        currentUser="editor@company.com"
        onClose={() => { setSelectedId(null); loadDocs() }}
      />
    )
  }

  // ── Document list ─────────────────────────────────────────────
  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      {/* Sidebar */}
      <aside style={s.sidebar}>
        <div style={s.logo}>🗂 Product Atlas</div>
        <div style={page === 'browse' ? s.navActive : s.nav} onClick={() => setPage('browse')}>📄 Browse</div>
        <div style={page === 'history' ? s.navActive : s.nav} onClick={() => setPage('history')}>🕒 History</div>
      </aside>

      {/* Main */}
      <main style={s.main}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
          <h1 style={{ fontSize: 22, fontWeight: 600 }}>{page === 'browse' ? 'Documents' : 'Processing History'}</h1>
          <button style={s.btnGhost} onClick={seed} disabled={seeding}>
            {seeding ? 'Seeding...' : '🌱 Seed Demo Data'}
          </button>
        </div>
        <p style={{ color: '#64748b', fontSize: 14, marginBottom: 24 }}>
          {page === 'browse' ? 'Browse and extract documents from SharePoint' : 'All processed documents — verification history'}
        </p>

        {/* Stats */}
        <div style={{ display: 'flex', gap: 16, marginBottom: 28 }}>
          {[
            { n: stats.total,      l: 'Total',       c: '#60a5fa' },
            { n: stats.extracted,  l: 'Extracted',   c: '#34d399' },
            { n: stats.inProgress, l: 'In Progress', c: '#60a5fa' },
            { n: stats.verified,   l: 'Verified',    c: '#4ade80' },
          ].map(({ n, l, c }) => (
            <div key={l} style={s.stat}>
              <div style={{ fontSize: 28, fontWeight: 700, color: c }}>{n}</div>
              <div style={{ fontSize: 12, color: '#64748b', marginTop: 2 }}>{l}</div>
            </div>
          ))}
        </div>

        {/* Table */}
        <div style={{ overflowX: 'auto' }}>
          <table style={s.table}>
            <thead>
              <tr>
                {['Document', 'Folder Path', 'Processed', 'Verification Status', 'Signed Off By', 'Actions']
                  .map(h => <th key={h} style={s.th}>{h}</th>)}
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={6} style={{ textAlign: 'center', padding: 40, color: '#64748b' }}>Loading...</td></tr>
              ) : docs.length === 0 ? (
                <tr><td colSpan={6} style={{ textAlign: 'center', padding: 40, color: '#64748b' }}>
                  No documents — click Seed Demo Data to start
                </td></tr>
              ) : docs.map(d => (
                <tr key={d.id} style={{ transition: '.15s' }}
                  onMouseEnter={e => (e.currentTarget.style.background = '#1a2035')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                  <td style={s.td}>
                    <span style={s.tag}>{d.file_type || '?'}</span>
                    <b style={{ marginLeft: 8 }}>{d.original_filename}</b>
                  </td>
                  <td style={{ ...s.td, color: '#64748b', fontSize: 12 }}>{d.sharepoint_path || '—'}</td>
                  <td style={{ ...s.td, color: '#94a3b8', fontSize: 12 }}>{fmtDate(d.processed_at)}</td>
                  <td style={s.td}><VerBadge status={d.verification_status} /></td>
                  <td style={{ ...s.td, fontSize: 12, color: d.verified_by ? '#4ade80' : '#475569' }}>
                    {d.verified_by || '—'}
                  </td>
                  <td style={{ ...s.td }}>
                    <div style={{ display: 'flex', gap: 8 }}>
                      {d.status === 'extracted' && (
                        <button style={s.btnPrimary} onClick={() => setSelectedId(d.id)}>View .md</button>
                      )}
                      <button style={s.btnGhost}>
                        {d.status === 'extracted' ? 'Reprocess' : 'Extract'}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </main>
    </div>
  )
}

const s: Record<string, React.CSSProperties> = {
  sidebar:   { width: 220, background: '#161b27', borderRight: '1px solid #1e2a3a', padding: '20px 0', position: 'sticky', top: 0, height: '100vh', flexShrink: 0 },
  logo:      { padding: '0 20px 24px', fontSize: 18, fontWeight: 700, color: '#60a5fa', borderBottom: '1px solid #1e2a3a', marginBottom: 16 },
  nav:       { padding: '10px 20px', cursor: 'pointer', color: '#94a3b8', fontSize: 14 },
  navActive: { padding: '10px 20px', cursor: 'pointer', color: '#e2e8f0', fontSize: 14, background: '#1e2a3a' },
  main:      { flex: 1, padding: 32 },
  stat:      { background: '#161b27', border: '1px solid #1e2a3a', borderRadius: 10, padding: '16px 24px', minWidth: 110 },
  table:     { width: '100%', borderCollapse: 'collapse', background: '#161b27', borderRadius: 10, overflow: 'hidden', border: '1px solid #1e2a3a' },
  th:        { textAlign: 'left', padding: '12px 16px', fontSize: 12, color: '#64748b', fontWeight: 600, textTransform: 'uppercase', borderBottom: '1px solid #1e2a3a' },
  td:        { padding: '12px 16px', fontSize: 13, borderBottom: '1px solid #1e2a3a', verticalAlign: 'middle' },
  tag:       { fontSize: 11, background: '#1e2a3a', color: '#64748b', padding: '2px 8px', borderRadius: 4 },
  btnPrimary:{ padding: '5px 12px', borderRadius: 7, border: 'none', background: '#2563eb', color: '#fff', cursor: 'pointer', fontWeight: 600, fontSize: 12 },
  btnGhost:  { padding: '5px 12px', borderRadius: 7, border: 'none', background: '#1e2a3a', color: '#94a3b8', cursor: 'pointer', fontWeight: 600, fontSize: 12 },
}
