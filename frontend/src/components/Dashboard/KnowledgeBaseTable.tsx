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
  Trash2,
} from 'lucide-react';
import { DocumentRecord, IngestionJob } from '../../types';
import { deleteDocument, fetchDocuments, fetchJob, fetchKnowledgeBaseStats, startSync } from '../../services/api';

export function KnowledgeBaseTable() {
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [stats, setStats] = useState<{ total_documents: number; total_chunks: number; last_sync: string | null } | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [syncing, setSyncing] = useState<boolean>(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);
  const [search, setSearch] = useState<string>('');
  const [typeFilter, setTypeFilter] = useState<string>('all');
  const [cloudFilter, setCloudFilter] = useState<string>('all');

  useEffect(() => {
    loadData();
  }, []);

  async function handleSync() {
    setSyncing(true);
    setSyncMessage('Checking every indexed document for changes...');
    try {
      let job: IngestionJob = await startSync();
      while (job.status === 'PENDING' || job.status === 'PROCESSING') {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        job = await fetchJob(job.job_id);
      }
      const s = job.result?.summary || {};
      const parts = Object.entries(s).map(([k, v]) => `${v} ${k.toLowerCase()}`);
      setSyncMessage(`Sync ${job.status === 'COMPLETED' ? 'complete' : 'finished with errors'}: ${parts.join(', ') || 'nothing to do'}.` +
        (job.error_message ? ` ${job.error_message}` : ''));
      await loadData();
    } catch (e) {
      setSyncMessage(`Sync failed: ${(e as Error).message}`);
    } finally {
      setSyncing(false);
    }
  }

  async function loadData() {
    setLoading(true);
    try {
      const [docs, s] = await Promise.all([fetchDocuments(), fetchKnowledgeBaseStats()]);
      setDocuments(docs.documents);
      setStats(s);
    } catch (e) {
      console.error('Failed to load knowledge base', e);
    } finally {
      setLoading(false);
    }
  }

  async function handleDelete(doc: DocumentRecord) {
    if (!window.confirm(`Remove "${doc.filename}" from the knowledge base? The file in your drive is not touched.`)) return;
    try {
      await deleteDocument(doc.id);
      await loadData();
    } catch (e) {
      setSyncMessage(`Could not remove the document: ${(e as Error).message}`);
    }
  }

  function formatBytes(bytes: number | null) {
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
      case 'gdoc':
        return <FileText size={16} color="#3b82f6" />;
      case 'xlsx':
      case 'xls':
      case 'gsheet':
        return <FileSpreadsheet size={16} color="#10b981" />;
      case 'pptx':
      case 'ppt':
      case 'gslides':
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
    const matchesCloud = cloudFilter === 'all' || (d.drive_type || 'onedrive') === cloudFilter;
    return matchesSearch && matchesType && matchesCloud;
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
          <p style={{ fontSize: '1.25rem', fontWeight: 700, marginTop: '0.25rem', color: 'var(--brand-cyan)' }}>
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
                background: 'var(--app-input-bg)',
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
                  background: 'var(--app-input-bg)',
                  border: '1px solid var(--border-color)',
                  color: 'var(--text-primary)',
                  fontSize: '0.8125rem',
                  outline: 'none',
                }}
              >
                <option value="all">All File Formats</option>
                <option value="pdf">PDF Documents</option>
                <option value="docx">Word (.docx / .gdoc)</option>
                <option value="xlsx">Excel (.xlsx / .gsheet)</option>
                <option value="pptx">PowerPoint (.pptx / .gslides)</option>
                <option value="txt">Text (.txt)</option>
              </select>
            </div>

            {/* Cloud Storage Provider Filter */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
              <select
                value={cloudFilter}
                onChange={(e) => setCloudFilter(e.target.value)}
                style={{
                  padding: '0.55rem 0.75rem',
                  borderRadius: 'var(--radius-md)',
                  background: 'var(--app-input-bg)',
                  border: '1px solid var(--border-color)',
                  color: 'var(--text-primary)',
                  fontSize: '0.8125rem',
                  outline: 'none',
                }}
              >
                <option value="all">All Cloud Storage</option>
                <option value="onedrive">OneDrive</option>
                <option value="google_drive">Google Drive</option>
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
              {syncing ? 'Syncing...' : 'Delta Sync All'}
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
                <th style={{ padding: '0.75rem 1rem' }}>Storage Source</th>
                <th style={{ padding: '0.75rem 1rem' }}>Folder Path</th>
                <th style={{ padding: '0.75rem 1rem' }}>Type</th>
                <th style={{ padding: '0.75rem 1rem' }}>Size</th>
                <th style={{ padding: '0.75rem 1rem' }}>Status</th>
                <th style={{ padding: '0.75rem 1rem', textAlign: 'right' }}>Cloud Action</th>
              </tr>
            </thead>
            <tbody>
              {filteredDocs.length === 0 ? (
                <tr>
                  <td colSpan={7} style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-muted)' }}>
                    No documents match. Use the drive browser to index files or folders.
                  </td>
                </tr>
              ) : (
                filteredDocs.map((doc) => {
                  const isGDrive = doc.drive_type === 'google_drive';
                  return (
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
                      <td style={{ padding: '0.85rem 1rem' }}>
                        {isGDrive ? (
                          <span
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: '0.35rem',
                              padding: '0.2rem 0.65rem',
                              borderRadius: '9999px',
                              background: 'rgba(16, 185, 129, 0.15)',
                              border: '1px solid rgba(16, 185, 129, 0.3)',
                              color: '#34d399',
                              fontSize: '0.725rem',
                              fontWeight: 600,
                            }}
                          >
                            <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: '#34d399' }} />
                            Google Drive
                          </span>
                        ) : (
                          <span
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: '0.35rem',
                              padding: '0.2rem 0.65rem',
                              borderRadius: '9999px',
                              background: 'rgba(0, 120, 212, 0.15)',
                              border: '1px solid rgba(0, 120, 212, 0.3)',
                              color: '#60a5fa',
                              fontSize: '0.725rem',
                              fontWeight: 600,
                            }}
                          >
                            <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: '#60a5fa' }} />
                            OneDrive
                          </span>
                        )}
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
                        <span
                          className={`badge ${doc.status === 'INDEXED' ? 'badge-success' : 'badge-info'}`}
                          title={doc.error_message || (doc.status === 'INDEXED' ? `${doc.chunk_count} passages indexed` : undefined)}
                          style={doc.status === 'FAILED' || doc.status === 'UNSUPPORTED' ? { background: 'rgba(244,63,94,0.15)', color: '#fb7185' } : undefined}
                        >
                          {doc.status === 'INDEXED' ? `Indexed (${doc.chunk_count})` : doc.status === 'UNSUPPORTED' ? 'Not supported' : doc.status}
                        </span>
                        {doc.error_message && doc.status !== 'INDEXED' && (
                          <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '0.25rem', maxWidth: '260px' }}>
                            {doc.error_message}
                          </div>
                        )}
                      </td>
                      <td style={{ padding: '0.85rem 1rem', textAlign: 'right' }}>
                        <div style={{ display: 'inline-flex', gap: '0.5rem' }}>
                          {doc.url && (
                            <a
                              href={doc.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="btn btn-secondary"
                              style={{ padding: '0.35rem 0.75rem', fontSize: '0.75rem' }}
                            >
                              <ExternalLink size={12} /> {isGDrive ? 'View in Drive' : 'View in OneDrive'}
                            </a>
                          )}
                          <button
                            onClick={() => handleDelete(doc)}
                            className="btn btn-secondary"
                            style={{ padding: '0.35rem 0.6rem', fontSize: '0.75rem' }}
                            title="Remove from knowledge base"
                            aria-label={`Remove ${doc.filename} from knowledge base`}
                          >
                            <Trash2 size={12} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
