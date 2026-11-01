import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import {
  ExternalLink,
  Search,
  Filter,
  RefreshCw,
  FolderSync,
  Folder,
  Trash2,
  X,
} from 'lucide-react';
import { DocumentRecord, DriveType, IngestionJob, KnowledgeBaseStats, TrackedFolder } from '../../types';
import {
  ApiError,
  deleteDocument,
  fetchDocuments,
  fetchJob,
  fetchKnowledgeBaseStats,
  fetchSyncStatus,
  fetchTrackedFolders,
  isJobRunning,
  startSync,
  untrackFolder,
} from '../../services/api';
import { FileTypeIcon } from '../FileTypeIcon';

interface Notice {
  kind: 'success' | 'error' | 'info';
  text: string;
}

const NOTICE_COLORS: Record<Notice['kind'], { background: string; border: string; color: string }> = {
  success: { background: 'var(--success-bg)', border: 'var(--success-border)', color: 'var(--success-text)' },
  error: { background: 'var(--danger-bg)', border: 'var(--danger-border)', color: 'var(--danger-text)' },
  info: { background: 'var(--info-bg)', border: 'var(--info-border)', color: 'var(--info-text)' },
};

const TYPE_LABELS: Record<string, string> = {
  pdf: 'PDF Documents',
  docx: 'Word (.docx)',
  doc: 'Word (.doc)',
  gdoc: 'Google Docs',
  xlsx: 'Excel (.xlsx)',
  xls: 'Excel (.xls)',
  gsheet: 'Google Sheets',
  csv: 'CSV (.csv)',
  pptx: 'PowerPoint (.pptx)',
  ppt: 'PowerPoint (.ppt)',
  gslides: 'Google Slides',
  txt: 'Text (.txt)',
  md: 'Markdown (.md)',
};

const SUMMARY_LABELS: Record<string, string> = {
  indexed: 'indexed',
  unchanged: 'unchanged',
  deleted: 'removed (deleted from the drive)',
  failed: 'failed',
  unsupported: 'not supported',
};

const SYNC_HELP =
  'Checks every indexed document against your drive: changed files are re-indexed, files deleted from the drive are removed, and new files in synced folders are added.';

const SYNC_POLL_MS = 1500;
const MAX_POLL_DELAY_MS = 15000;
const MAX_POLL_FAILURES = 6;

function describeSummary(summary: Record<string, number> = {}): string {
  const keys = [...Object.keys(SUMMARY_LABELS), ...Object.keys(summary).filter((k) => !(k in SUMMARY_LABELS))];
  const parts = keys.filter((k) => summary[k]).map((k) => `${summary[k]} ${SUMMARY_LABELS[k] ?? k}`);
  return parts.join(', ') || 'nothing changed';
}

function formatRelative(dateStr: string | null | undefined): string | null {
  if (!dateStr) return null;
  const time = new Date(dateStr).getTime();
  if (isNaN(time)) return null;
  const minutes = Math.floor((Date.now() - time) / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)}h ago`;
  return new Date(time).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

/** Resolves after `ms`, or immediately when the signal aborts. */
function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      'abort',
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true }
    );
  });
}

const isClientError = (e: unknown) =>
  e instanceof ApiError && e.status >= 400 && e.status < 500 && e.status !== 408 && e.status !== 429;

function DriveBadge({ drive }: { drive?: DriveType }) {
  const isGDrive = drive === 'google_drive';
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '0.35rem',
        padding: '0.2rem 0.65rem',
        borderRadius: '9999px',
        background: isGDrive ? 'rgba(16, 185, 129, 0.15)' : 'rgba(0, 120, 212, 0.15)',
        border: `1px solid ${isGDrive ? 'rgba(16, 185, 129, 0.3)' : 'rgba(0, 120, 212, 0.3)'}`,
        color: isGDrive ? 'var(--success-text)' : 'var(--info-text)',
        fontSize: '0.725rem',
        fontWeight: 600,
        whiteSpace: 'nowrap',
      }}
    >
      <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: 'currentColor' }} aria-hidden="true" />
      {isGDrive ? 'Google Drive' : 'OneDrive'}
    </span>
  );
}

const statCardLabel: CSSProperties = { fontSize: '0.8125rem', color: 'var(--text-secondary)' };
const statCardValue: CSSProperties = { fontSize: '1.75rem', fontWeight: 800, marginTop: '0.25rem' };
const statCardHint: CSSProperties = { fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.15rem' };
const selectStyle: CSSProperties = {
  padding: '0.55rem 0.75rem',
  borderRadius: 'var(--radius-md)',
  background: 'var(--app-input-bg)',
  border: '1px solid var(--border-color)',
  color: 'var(--text-primary)',
  fontSize: '0.8125rem',
};

export function KnowledgeBaseTable({ refreshToken = 0 }: { refreshToken?: number }) {
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [totalDocuments, setTotalDocuments] = useState<number>(0);
  const [stats, setStats] = useState<KnowledgeBaseStats | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [folders, setFolders] = useState<TrackedFolder[]>([]);
  const [foldersError, setFoldersError] = useState<string | null>(null);
  const [untrackingId, setUntrackingId] = useState<string | null>(null);
  const [syncing, setSyncing] = useState<boolean>(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [search, setSearch] = useState<string>('');
  const [typeFilter, setTypeFilter] = useState<string>('all');
  const [cloudFilter, setCloudFilter] = useState<string>('all');

  const mountedRef = useRef(true);
  const loadRequestRef = useRef(0);
  const syncingRef = useRef(false);
  const syncAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      syncAbortRef.current?.abort(); // stop polling the sync job
    };
  }, []);

  const loadFolders = useCallback(async () => {
    try {
      const tracked = await fetchTrackedFolders();
      if (!mountedRef.current) return;
      setFolders(tracked);
      setFoldersError(null);
    } catch (e) {
      if (mountedRef.current) setFoldersError((e as Error).message);
    }
  }, []);

  const loadData = useCallback(async () => {
    const request = ++loadRequestRef.current;
    const isCurrent = () => mountedRef.current && request === loadRequestRef.current;
    setLoading(true);
    setLoadError(null);
    loadFolders();
    try {
      const [docs, s] = await Promise.all([fetchDocuments(), fetchKnowledgeBaseStats()]);
      if (!isCurrent()) return;
      setDocuments(docs.documents);
      setTotalDocuments(docs.total ?? docs.documents.length);
      setStats(s);
    } catch (e) {
      if (isCurrent()) setLoadError((e as Error).message);
    } finally {
      if (isCurrent()) setLoading(false);
    }
  }, [loadFolders]);

  // Initial load, and a reload whenever the parent signals new documents (e.g. an indexing job finished).
  useEffect(() => {
    loadData();
  }, [loadData, refreshToken]);

  /** Follows a sync job to the end, then reloads; cancelled when the table unmounts. */
  const followSync = useCallback(
    async (initial: IngestionJob) => {
      syncAbortRef.current?.abort();
      const controller = new AbortController();
      syncAbortRef.current = controller;
      const { signal } = controller;
      syncingRef.current = true;
      setSyncing(true);
      setNotice({ kind: 'info', text: 'Syncing: checking every indexed document for changes and looking for new files in synced folders...' });
      try {
        let job = initial;
        let failures = 0;
        while (isJobRunning(job.status)) {
          await sleep(failures ? Math.min(SYNC_POLL_MS * 2 ** failures, MAX_POLL_DELAY_MS) : SYNC_POLL_MS, signal);
          if (signal.aborted) return;
          try {
            job = await fetchJob(job.job_id);
            failures = 0;
          } catch (e) {
            if (isClientError(e) || ++failures >= MAX_POLL_FAILURES) throw e;
          }
          if (signal.aborted) return;
        }
        const summary = describeSummary(job.result?.summary ?? {});
        if (job.status === 'COMPLETED') {
          setNotice({ kind: 'success', text: `Sync complete: ${summary}.` });
        } else if (job.status === 'COMPLETED_WITH_ERRORS') {
          setNotice({ kind: 'error', text: `Sync finished with errors: ${summary}.${job.error_message ? ` ${job.error_message}` : ''}` });
        } else {
          setNotice({ kind: 'error', text: `Sync failed${job.error_message ? `: ${job.error_message}` : '.'}` });
        }
        await loadData();
      } catch (e) {
        if (!signal.aborted) setNotice({ kind: 'error', text: `Sync failed: ${(e as Error).message}` });
      } finally {
        if (syncAbortRef.current === controller) {
          syncAbortRef.current = null;
          syncingRef.current = false;
          if (mountedRef.current) setSyncing(false);
        }
      }
    },
    [loadData]
  );

  // Resume showing a sync that is still running (e.g. the drawer was closed while it ran).
  useEffect(() => {
    let cancelled = false;
    fetchSyncStatus()
      .then(({ last_sync }) => {
        if (!cancelled && last_sync && isJobRunning(last_sync.status) && !syncingRef.current) followSync(last_sync);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [followSync]);

  async function handleSync() {
    if (syncingRef.current) return; // double click
    syncingRef.current = true;
    setSyncing(true);
    try {
      const job = await startSync();
      if (!mountedRef.current) return;
      followSync(job);
    } catch (e) {
      syncingRef.current = false;
      if (!mountedRef.current) return;
      setSyncing(false);
      setNotice({ kind: 'error', text: `Sync failed: ${(e as Error).message}` });
    }
  }

  async function handleDelete(doc: DocumentRecord) {
    if (
      !window.confirm(
        `Remove "${doc.filename}" from the knowledge base?\n\nThe file in your drive is not touched. It stays out of future syncs until you select it for indexing again.`
      )
    )
      return;
    try {
      await deleteDocument(doc.id);
      if (!mountedRef.current) return;
      setNotice({ kind: 'success', text: `Removed "${doc.filename}" from the knowledge base.` });
      await loadData();
    } catch (e) {
      if (mountedRef.current) setNotice({ kind: 'error', text: `Could not remove the document: ${(e as Error).message}` });
    }
  }

  async function handleUntrack(folder: TrackedFolder) {
    const label = folder.path || folder.name;
    if (
      !window.confirm(
        `Stop syncing "${label}"?\n\nNew files added to this folder will no longer be indexed. Documents already indexed from it stay in your knowledge base.`
      )
    )
      return;
    setUntrackingId(folder.id);
    try {
      await untrackFolder(folder.id);
      if (!mountedRef.current) return;
      setFolders((prev) => prev.filter((f) => f.id !== folder.id));
      setNotice({ kind: 'success', text: `Stopped syncing "${label}". Its indexed documents stay searchable.` });
    } catch (e) {
      if (mountedRef.current) setNotice({ kind: 'error', text: `Could not stop syncing the folder: ${(e as Error).message}` });
    } finally {
      if (mountedRef.current) setUntrackingId(null);
    }
  }

  function formatBytes(bytes: number | null) {
    if (!bytes) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
  }

  // File-type filter options come from the documents actually loaded.
  const fileTypes = useMemo(
    () => Array.from(new Set(documents.map((d) => (d.file_type || '').toLowerCase()).filter(Boolean))).sort(),
    [documents]
  );
  const activeType = fileTypes.includes(typeFilter) ? typeFilter : 'all';

  const query = search.toLowerCase();
  const filteredDocs = documents.filter((d) => {
    const matchesSearch = d.filename.toLowerCase().includes(query) || (d.folder_path || '').toLowerCase().includes(query);
    const matchesType = activeType === 'all' || (d.file_type || '').toLowerCase() === activeType;
    const matchesCloud = cloudFilter === 'all' || (d.drive_type || 'onedrive') === cloudFilter;
    return matchesSearch && matchesType && matchesCloud;
  });

  const byStatus = stats?.by_status ?? {};
  const indexedCount = byStatus.INDEXED ?? stats?.total_documents ?? 0;
  const pendingCount = byStatus.PENDING ?? 0;
  const failedCount = byStatus.FAILED ?? 0;
  const unsupportedCount = byStatus.UNSUPPORTED ?? 0;
  const attentionCount = failedCount + unsupportedCount;
  const noticeColors = notice ? NOTICE_COLORS[notice.kind] : null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
      {/* Top Stats Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(220px, 100%), 1fr))', gap: '1rem' }}>
        <div className="glass-card" style={{ padding: '1.25rem' }}>
          <span style={statCardLabel}>Indexed documents</span>
          <p style={{ ...statCardValue, color: 'var(--app-text)' }}>{indexedCount}</p>
          {pendingCount > 0 && <p style={statCardHint}>{pendingCount} waiting to be indexed</p>}
        </div>
        <div className="glass-card" style={{ padding: '1.25rem' }}>
          <span style={statCardLabel}>Searchable passages</span>
          <p style={{ ...statCardValue, color: 'var(--success-text)' }}>{stats?.total_chunks ?? 0}</p>
        </div>
        <div className="glass-card" style={{ padding: '1.25rem' }}>
          <span style={statCardLabel}>Needs attention</span>
          <p style={{ ...statCardValue, color: attentionCount > 0 ? 'var(--danger-text)' : 'var(--app-text)' }}>{attentionCount}</p>
          <p style={statCardHint}>
            {attentionCount > 0 ? `${failedCount} failed · ${unsupportedCount} not supported` : 'No failed or unsupported files'}
          </p>
        </div>
        <div className="glass-card" style={{ padding: '1.25rem' }}>
          <span style={statCardLabel}>Synced folders</span>
          <p style={{ ...statCardValue, color: 'var(--info-text)' }}>{folders.length}</p>
          <p style={statCardHint}>New files are added on sync</p>
        </div>
      </div>

      {/* Synced (tracked) folders */}
      <div className="glass-panel" style={{ padding: 'clamp(1rem, 4vw, 1.75rem)' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', flexWrap: 'wrap', gap: '0.5rem', marginBottom: '0.85rem' }}>
          <h4 style={{ fontSize: '0.95rem', fontWeight: 700 }}>Synced folders</h4>
          <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Folders you selected for indexing. New files in them are added each time you sync.</span>
        </div>
        {foldersError ? (
          <p role="alert" style={{ fontSize: '0.8125rem', color: 'var(--danger-text)' }}>
            Could not load synced folders: {foldersError}
          </p>
        ) : folders.length === 0 ? (
          <p style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>
            No synced folders yet. Select a folder in the drive browser to index it and keep it in sync.
          </p>
        ) : (
          <ul style={{ listStyle: 'none', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            {folders.map((folder) => {
              const synced = formatRelative(folder.last_synced_at);
              return (
                <li
                  key={folder.id}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    flexWrap: 'wrap',
                    gap: '0.6rem',
                    padding: '0.6rem 0.75rem',
                    border: '1px solid var(--border-color)',
                    borderRadius: 'var(--radius-md)',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', minWidth: 0, flexWrap: 'wrap' }}>
                    <Folder size={16} color="#0078d4" aria-hidden="true" style={{ flexShrink: 0 }} />
                    <DriveBadge drive={folder.drive_type} />
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8125rem', color: 'var(--text-secondary)', overflowWrap: 'anywhere' }}>
                      {folder.path || folder.name}
                    </span>
                    <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                      {synced ? `Last synced ${synced}` : 'Not synced yet'}
                    </span>
                  </div>
                  <button
                    type="button"
                    className="btn btn-secondary"
                    style={{ padding: '0.35rem 0.75rem', fontSize: '0.75rem' }}
                    onClick={() => handleUntrack(folder)}
                    disabled={untrackingId === folder.id}
                    title="New files in this folder will no longer be indexed; documents already indexed stay"
                  >
                    {untrackingId === folder.id ? 'Stopping...' : 'Stop syncing'}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {/* Filter and Table Panel */}
      <div className="glass-panel" style={{ padding: 'clamp(1rem, 4vw, 1.75rem)' }}>
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
          <div style={{ position: 'relative', flex: 1, minWidth: 'min(240px, 100%)' }}>
            <Search size={16} color="#64748b" style={{ position: 'absolute', left: '12px', top: '12px' }} aria-hidden="true" />
            <input
              type="text"
              placeholder="Search indexed documents or folders..."
              aria-label="Search indexed documents or folders"
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
              }}
            />
          </div>

          <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '0.75rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
              <Filter size={14} color="#64748b" aria-hidden="true" />
              <select
                value={activeType}
                onChange={(e) => setTypeFilter(e.target.value)}
                aria-label="Filter by file type"
                style={{ ...selectStyle, padding: '0.55rem 1rem' }}
              >
                <option value="all">All File Formats</option>
                {fileTypes.map((type) => (
                  <option key={type} value={type}>
                    {TYPE_LABELS[type] ?? `.${type}`}
                  </option>
                ))}
              </select>
            </div>

            {/* Cloud Storage Provider Filter */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
              <select value={cloudFilter} onChange={(e) => setCloudFilter(e.target.value)} aria-label="Filter by cloud storage" style={selectStyle}>
                <option value="all">All Cloud Storage</option>
                <option value="onedrive">OneDrive</option>
                <option value="google_drive">Google Drive</option>
              </select>
            </div>

            <button type="button" className="btn btn-secondary" onClick={loadData} disabled={loading || syncing}>
              <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> Refresh
            </button>

            <button
              type="button"
              className="btn btn-primary"
              onClick={handleSync}
              disabled={syncing || loading}
              title={SYNC_HELP}
              style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}
            >
              <FolderSync size={14} className={syncing ? 'animate-spin' : ''} />
              {syncing ? 'Syncing...' : 'Sync now'}
            </button>
          </div>
        </div>

        {/* Sync / action status banner */}
        {notice && noticeColors && (
          <div
            role={notice.kind === 'error' ? 'alert' : 'status'}
            style={{
              padding: '0.75rem 1rem',
              marginBottom: '1rem',
              borderRadius: 'var(--radius-md)',
              background: noticeColors.background,
              border: `1px solid ${noticeColors.border}`,
              color: noticeColors.color,
              fontSize: '0.85rem',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: '0.75rem',
            }}
          >
            <span style={{ overflowWrap: 'anywhere' }}>{notice.text}</span>
            <button
              type="button"
              onClick={() => setNotice(null)}
              aria-label="Dismiss message"
              title="Dismiss"
              style={{
                background: 'none',
                border: 'none',
                color: 'inherit',
                cursor: 'pointer',
                display: 'flex',
                opacity: 0.8,
                flexShrink: 0,
              }}
            >
              <X size={14} />
            </button>
          </div>
        )}

        {totalDocuments > documents.length && !loadError && (
          <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '0.75rem' }}>
            Showing the {documents.length} most recently updated of {totalDocuments} documents.
          </p>
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
              {loadError ? (
                <tr>
                  <td colSpan={7} role="alert" style={{ textAlign: 'center', padding: '3rem 1rem', color: 'var(--danger-text)' }}>
                    <p style={{ marginBottom: '0.85rem' }}>Could not load your documents: {loadError}</p>
                    <button type="button" className="btn btn-secondary" onClick={loadData} disabled={loading}>
                      <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> Try again
                    </button>
                  </td>
                </tr>
              ) : loading && documents.length === 0 ? (
                <tr>
                  <td colSpan={7} role="status" style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-muted)' }}>
                    Loading documents...
                  </td>
                </tr>
              ) : filteredDocs.length === 0 ? (
                <tr>
                  <td colSpan={7} style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-muted)' }}>
                    {documents.length === 0
                      ? 'Nothing is indexed yet. Use the drive browser to index files or folders.'
                      : 'No documents match. Change the search or filters.'}
                  </td>
                </tr>
              ) : (
                filteredDocs.map((doc) => {
                  const isGDrive = doc.drive_type === 'google_drive';
                  const problem = doc.status === 'FAILED' || doc.status === 'UNSUPPORTED';
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
                        <FileTypeIcon type={doc.file_type || ''} />
                        <span>{doc.filename}</span>
                      </td>
                      <td style={{ padding: '0.85rem 1rem' }}>
                        <DriveBadge drive={doc.drive_type} />
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
                          className={`badge ${doc.status === 'INDEXED' ? 'badge-success' : problem ? 'badge-danger' : 'badge-info'}`}
                          title={doc.error_message || (doc.status === 'INDEXED' ? `${doc.chunk_count} passages indexed` : undefined)}
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
                            type="button"
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
