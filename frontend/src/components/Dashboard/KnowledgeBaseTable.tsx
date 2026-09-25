import { useState, useEffect } from 'react';
import {
  FileText,
  FileSpreadsheet,
  Presentation,
  ExternalLink,
  Search,
  Filter,
  RefreshCw,
  FolderSync,
  CheckCircle2,
} from 'lucide-react';
import { DocumentRecord } from '../../types';

export function KnowledgeBaseTable() {
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [stats, setStats] = useState<{ total_documents: number; total_chunks: number; last_sync: string | null } | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [syncing, setSyncing] = useState<boolean>(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);
  const [search, setSearch] = useState<string>('');
  const [typeFilter, setTypeFilter] = useState<string>('all');

  useEffect(() => {
    loadData();
  }, []);

  async function handleSync() {
    setSyncing(true);
    setSyncMessage(null);
    try {
      const res = await fetch('/api/sync/start?background=false', { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        const s = data.summary;
        if (s) {
          setSyncMessage(`Sync Complete: ${s.total_scanned} scanned (${s.new_files} new, ${s.updated_files} updated, ${s.unchanged_files} unchanged) in ${s.duration_seconds}s.`);
        } else {
          setSyncMessage(data.message || 'Sync complete.');
        }
        await loadData();
      }
    } catch (e) {
      console.error('Failed to trigger sync', e);
      setSyncMessage('Failed to run incremental sync.');
    } finally {
      setSyncing(false);
    }
  }

  async function loadData() {
    setLoading(true);
    try {
      const [docsRes, statsRes] = await Promise.all([
        fetch('/api/documents'),
        fetch('/api/documents/stats'),
      ]);
      if (docsRes.ok && statsRes.ok) {
        const dData = await docsRes.json();
        const sData = await statsRes.json();
        setDocuments(dData.documents || []);
        setStats(sData);
      }
    } catch (e) {
      console.error('Failed to load KB data', e);
    } finally {
      setLoading(false);
    }
  }

  function formatBytes(bytes: number) {
    if (!bytes) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
  }

  function renderFileIcon(type: string) {
    switch (type.toLowerCase()) {
      case 'pdf':
        return <FileText size={16} color="#f43f5e" />;
      case 'docx':
      case 'doc':
        return <FileText size={16} color="#3b82f6" />;
      case 'xlsx':
      case 'xls':
        return <FileSpreadsheet size={16} color="#10b981" />;
      case 'pptx':
      case 'ppt':
        return <Presentation size={16} color="#f59e0b" />;
      default:
        return <FileText size={16} color="#a855f7" />;
    }
  }

  const filteredDocs = documents.filter((d) => {
    const matchesSearch =
      d.filename.toLowerCase().includes(search.toLowerCase()) ||
      d.folder_path.toLowerCase().includes(search.toLowerCase());
    const matchesType = typeFilter === 'all' || d.file_type.toLowerCase() === typeFilter.toLowerCase();
    return matchesSearch && matchesType;
  });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
      {/* Top Stats Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '1rem' }}>
        <div className="glass-card" style={{ padding: '1.25rem' }}>
          <span style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>Total Indexed Documents</span>
          <p style={{ fontSize: '1.75rem', fontWeight: 800, marginTop: '0.25rem', color: '#f0f4fc' }}>
            {stats?.total_documents || documents.length}
          </p>
        </div>
        <div className="glass-card" style={{ padding: '1.25rem' }}>
          <span style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>Vector Chunks (1536 dims)</span>
          <p style={{ fontSize: '1.75rem', fontWeight: 800, marginTop: '0.25rem', color: '#10b981' }}>
            {stats?.total_chunks || 0}
          </p>
        </div>
        <div className="glass-card" style={{ padding: '1.25rem' }}>
          <span style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>Storage Engine</span>
          <p style={{ fontSize: '1.25rem', fontWeight: 700, marginTop: '0.25rem', color: '#38bdf8' }}>
            PostgreSQL + pgvector
          </p>
        </div>
        <div className="glass-card" style={{ padding: '1.25rem' }}>
          <span style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>Index Status</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginTop: '0.4rem' }}>
            <span className="badge badge-success">
              <CheckCircle2 size={12} /> Healthy & Grounded
            </span>
          </div>
        </div>
      </div>

      {/* Filter and Table Panel */}
      <div className="glass-panel" style={{ padding: '1.75rem' }}>
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            gap: '1rem',
            marginBottom: '1.25rem',
            flexWrap: 'wrap',
          }}
        >
          <div style={{ position: 'relative', flex: 1, minWidth: '240px' }}>
            <Search size={16} color="#64748b" style={{ position: 'absolute', left: '12px', top: '12px' }} />
            <input
              type="text"
              placeholder="Search indexed documents or folders..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{
                width: '100%',
                padding: '0.6rem 0.75rem 0.6rem 2.25rem',
                borderRadius: 'var(--radius-md)',
                background: 'rgba(15, 23, 42, 0.6)',
                border: '1px solid var(--border-color)',
                color: 'var(--text-primary)',
                fontSize: '0.875rem',
                outline: 'none',
              }}
            />
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
              <Filter size={14} color="#64748b" />
              <select
                value={typeFilter}
                onChange={(e) => setTypeFilter(e.target.value)}
                style={{
                  padding: '0.55rem 1rem',
                  borderRadius: 'var(--radius-md)',
                  background: 'rgba(15, 23, 42, 0.6)',
                  border: '1px solid var(--border-color)',
                  color: 'var(--text-primary)',
                  fontSize: '0.8125rem',
                  outline: 'none',
                }}
              >
                <option value="all">All File Formats</option>
                <option value="pdf">PDF Documents</option>
                <option value="docx">Word (.docx)</option>
                <option value="xlsx">Excel (.xlsx)</option>
                <option value="pptx">PowerPoint (.pptx)</option>
                <option value="txt">Text (.txt)</option>
              </select>
            </div>

            <button className="btn btn-secondary" onClick={loadData} disabled={loading || syncing}>
              <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> Refresh
            </button>

            <button
              className="btn btn-primary"
              onClick={handleSync}
              disabled={syncing || loading}
              style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}
            >
              <FolderSync size={14} className={syncing ? 'animate-spin' : ''} />
              {syncing ? 'Syncing...' : 'Sync OneDrive'}
            </button>
          </div>
        </div>

        {/* Sync Status Banner */}
        {syncMessage && (
          <div
            style={{
              padding: '0.75rem 1rem',
              marginBottom: '1rem',
              borderRadius: 'var(--radius-md)',
              background: 'rgba(16, 185, 129, 0.1)',
              border: '1px solid rgba(16, 185, 129, 0.3)',
              color: '#34d399',
              fontSize: '0.85rem',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
            }}
          >
            <span>{syncMessage}</span>
            <button
              onClick={() => setSyncMessage(null)}
              style={{
                background: 'none',
                border: 'none',
                color: '#34d399',
                cursor: 'pointer',
                fontSize: '0.85rem',
                opacity: 0.8,
              }}
            >
              ✕
            </button>
          </div>
        )}

        {/* Documents Table */}
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '0.875rem' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border-color)', color: 'var(--text-secondary)' }}>
                <th style={{ padding: '0.75rem 1rem' }}>Filename</th>
                <th style={{ padding: '0.75rem 1rem' }}>Folder Path</th>
                <th style={{ padding: '0.75rem 1rem' }}>Type</th>
                <th style={{ padding: '0.75rem 1rem' }}>Size</th>
                <th style={{ padding: '0.75rem 1rem' }}>Status</th>
                <th style={{ padding: '0.75rem 1rem', textAlign: 'right' }}>OneDrive Action</th>
              </tr>
            </thead>
            <tbody>
              {filteredDocs.length === 0 ? (
                <tr>
                  <td colSpan={6} style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-muted)' }}>
                    No indexed documents match your query. Go to the OneDrive browser to index folders.
                  </td>
                </tr>
              ) : (
                filteredDocs.map((doc) => (
                  <tr
                    key={doc.id}
                    style={{
                      borderBottom: '1px solid var(--border-color)',
                      transition: 'background 0.15s ease',
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = 'rgba(255,255,255,0.02)')}
                    onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
                  >
                    <td style={{ padding: '0.85rem 1rem', display: 'flex', alignItems: 'center', gap: '0.5rem', fontWeight: 600 }}>
                      {renderFileIcon(doc.file_type)}
                      <span>{doc.filename}</span>
                    </td>
                    <td style={{ padding: '0.85rem 1rem', color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)', fontSize: '0.8125rem' }}>
                      {doc.folder_path}
                    </td>
                    <td style={{ padding: '0.85rem 1rem' }}>
                      <span className="badge badge-info" style={{ textTransform: 'uppercase' }}>
                        {doc.file_type}
                      </span>
                    </td>
                    <td style={{ padding: '0.85rem 1rem', color: 'var(--text-muted)' }}>
                      {formatBytes(doc.file_size)}
                    </td>
                    <td style={{ padding: '0.85rem 1rem' }}>
                      <span className="badge badge-success">
                        {doc.status}
                      </span>
                    </td>
                    <td style={{ padding: '0.85rem 1rem', textAlign: 'right' }}>
                      <a
                        href={doc.onedrive_url || '#'}
                        target="_blank"
                        rel="noreferrer"
                        className="btn btn-secondary"
                        style={{ padding: '0.35rem 0.75rem', fontSize: '0.75rem' }}
                      >
                        <ExternalLink size={12} /> View in OneDrive
                      </a>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
