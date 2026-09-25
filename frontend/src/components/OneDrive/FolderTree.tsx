import { useState, useEffect } from 'react';
import {
  Folder,
  FolderOpen,
  FileText,
  FileSpreadsheet,
  Presentation,
  CheckSquare,
  Square,
  ChevronRight,
  ChevronDown,
  ExternalLink,
  Search,
  Database,
  RefreshCw,
  Sparkles,
} from 'lucide-react';
import { OneDriveItem, DriveType } from '../../types';

interface FolderTreeProps {
  onIndexSelection: (selectedIds: string[], selectedItems: OneDriveItem[]) => void;
  isIndexing?: boolean;
}

export function FolderTree({ onIndexSelection, isIndexing = false }: FolderTreeProps) {
  const [driveType, setDriveType] = useState<DriveType>('onedrive');
  const [items, setItems] = useState<OneDriveItem[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [expandedFolders, setExpandedFolders] = useState<Record<string, boolean>>({
    folder_hr: true,
    folder_finance: true,
    folder_engineering: true,
    gdrive_folder_corporate: true,
    gdrive_folder_tech: true,
  });
  const [selectedIds, setSelectedIds] = useState<Record<string, boolean>>({});
  const [searchQuery, setSearchQuery] = useState<string>('');

  useEffect(() => {
    fetchTree(driveType);
  }, [driveType]);

  async function fetchTree(targetDrive: DriveType = driveType) {
    setLoading(true);
    try {
      const endpoint = targetDrive === 'google_drive' ? '/api/gdrive/tree' : '/api/onedrive/tree';
      const res = await fetch(endpoint);
      if (res.ok) {
        const data = await res.json();
        setItems(data.items || []);
        // Auto-select files by default for convenience
        const initialSelected: Record<string, boolean> = {};
        for (const item of data.items) {
          if (!item.is_folder) initialSelected[item.id] = true;
          if (item.children) {
            for (const child of item.children) {
              if (!child.is_folder) initialSelected[child.id] = true;
            }
          }
        }
        setSelectedIds(initialSelected);
      }
    } catch (e) {
      console.error(`Failed to fetch ${targetDrive} tree`, e);
    } finally {
      setLoading(false);
    }
  }

  function toggleFolder(folderId: string) {
    setExpandedFolders((prev) => ({ ...prev, [folderId]: !prev[folderId] }));
  }

  function toggleItemSelection(item: OneDriveItem) {
    if (item.is_folder) {
      // Toggle folder and all its direct children
      const willSelect = !selectedIds[item.id];
      const updated = { ...selectedIds, [item.id]: willSelect };
      if (item.children) {
        for (const child of item.children) {
          updated[child.id] = willSelect;
        }
      }
      setSelectedIds(updated);
    } else {
      setSelectedIds((prev) => ({ ...prev, [item.id]: !prev[item.id] }));
    }
  }

  function getAllSelectedItems(): OneDriveItem[] {
    const selected: OneDriveItem[] = [];
    function collect(list: OneDriveItem[]) {
      for (const item of list) {
        if (!item.is_folder && selectedIds[item.id]) {
          selected.push(item);
        }
        if (item.children) collect(item.children);
      }
    }
    collect(items);
    return selected;
  }

  const selectedItems = getAllSelectedItems();

  function formatBytes(bytes?: number) {
    if (!bytes) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
  }

  function renderFileIcon(name: string) {
    const ext = name.split('.').pop()?.toLowerCase();
    switch (ext) {
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

  function renderItem(item: OneDriveItem, level: number = 0) {
    const isExpanded = expandedFolders[item.id];
    const isSelected = selectedIds[item.id];
    const matchesSearch =
      !searchQuery ||
      item.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      (item.children &&
        item.children.some((c) =>
          c.name.toLowerCase().includes(searchQuery.toLowerCase())
        ));

    if (!matchesSearch) return null;

    return (
      <div key={item.id} style={{ marginLeft: `${level * 1.5}rem` }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '0.5rem 0.75rem',
            borderRadius: 'var(--radius-sm)',
            background: isSelected ? 'rgba(0, 120, 212, 0.08)' : 'transparent',
            border: isSelected ? '1px solid rgba(0, 120, 212, 0.2)' : '1px solid transparent',
            marginBottom: '0.25rem',
            transition: 'all 0.15s ease',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flex: 1 }}>
            {/* Checkbox */}
            <button
              onClick={() => toggleItemSelection(item)}
              style={{
                background: 'transparent',
                border: 'none',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                color: isSelected ? '#38bdf8' : 'var(--text-muted)',
              }}
            >
              {isSelected ? <CheckSquare size={16} /> : <Square size={16} />}
            </button>

            {/* Folder Toggle */}
            {item.is_folder ? (
              <div
                onClick={() => toggleFolder(item.id)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.4rem',
                  cursor: 'pointer',
                  fontWeight: 600,
                  color: 'var(--text-primary)',
                }}
              >
                {isExpanded ? <ChevronDown size={14} color="#94a3b8" /> : <ChevronRight size={14} color="#94a3b8" />}
                {isExpanded ? <FolderOpen size={18} color="#0078d4" /> : <Folder size={18} color="#0078d4" />}
                <span>{item.name}</span>
                <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                  ({item.children?.length || 0} files)
                </span>
              </div>
            ) : (
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                {renderFileIcon(item.name)}
                <span style={{ fontSize: '0.875rem', color: 'var(--text-primary)' }}>{item.name}</span>
              </div>
            )}
          </div>

          {/* Metadata & Direct Link */}
          {!item.is_folder && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
              <span>{formatBytes(item.size)}</span>
              {item.web_url && (
                <a
                  href={item.web_url}
                  target="_blank"
                  rel="noreferrer"
                  title="Open original document in OneDrive"
                  style={{
                    color: 'var(--text-secondary)',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.2rem',
                    textDecoration: 'none',
                  }}
                >
                  <ExternalLink size={12} /> OneDrive
                </a>
              )}
            </div>
          )}
        </div>

        {/* Render Folder Children */}
        {item.is_folder && isExpanded && item.children && (
          <div>{item.children.map((child) => renderItem(child, level + 1))}</div>
        )}
      </div>
    );
  }

  return (
    <div className="glass-panel" style={{ padding: '1.75rem' }}>
      {/* Cloud Storage Provider Switcher */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: '1rem',
          paddingBottom: '1.25rem',
          marginBottom: '1.25rem',
          borderBottom: '1px solid var(--border-color)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
          <span style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-secondary)' }}>
            Active Cloud Storage:
          </span>
          <div
            style={{
              display: 'inline-flex',
              padding: '3px',
              background: 'rgba(15, 23, 42, 0.7)',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-color)',
            }}
          >
            <button
              onClick={() => setDriveType('onedrive')}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.45rem',
                padding: '0.45rem 0.9rem',
                borderRadius: 'var(--radius-sm)',
                border: 'none',
                background: driveType === 'onedrive' ? 'linear-gradient(135deg, #0078D4 0%, #005A9E 100%)' : 'transparent',
                color: driveType === 'onedrive' ? '#ffffff' : 'var(--text-secondary)',
                fontWeight: 600,
                fontSize: '0.8rem',
                cursor: 'pointer',
                transition: 'all 0.15s ease',
              }}
            >
              <svg width="14" height="14" viewBox="0 0 21 21" fill="none">
                <rect x="1" y="1" width="9" height="9" fill="#f25022" />
                <rect x="11" y="1" width="9" height="9" fill="#7fba00" />
                <rect x="1" y="11" width="9" height="9" fill="#00a4ef" />
                <rect x="11" y="11" width="9" height="9" fill="#ffb900" />
              </svg>
              Microsoft OneDrive
            </button>
            <button
              onClick={() => setDriveType('google_drive')}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.45rem',
                padding: '0.45rem 0.9rem',
                borderRadius: 'var(--radius-sm)',
                border: 'none',
                background: driveType === 'google_drive' ? 'linear-gradient(135deg, #0f9d58 0%, #0b8043 100%)' : 'transparent',
                color: driveType === 'google_drive' ? '#ffffff' : 'var(--text-secondary)',
                fontWeight: 600,
                fontSize: '0.8rem',
                cursor: 'pointer',
                transition: 'all 0.15s ease',
              }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
                <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
                <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"/>
                <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"/>
              </svg>
              Google Drive
            </button>
          </div>
        </div>

        <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
          Browsing {driveType === 'google_drive' ? 'Google Shared Drive' : 'OneDrive Root'} &bull; Delta Change Detection Active
        </span>
      </div>

      {/* Top Search & Actions Bar */}
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
            placeholder="Filter files by name..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
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
          <button
            className="btn btn-secondary"
            onClick={() => fetchTree()}
            disabled={loading}
            title="Refresh cloud items"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> Refresh
          </button>

          <button
            className="btn btn-primary"
            disabled={selectedItems.length === 0 || isIndexing}
            onClick={() => onIndexSelection(selectedItems.map((i) => i.id), selectedItems)}
          >
            <Database size={16} />
            {isIndexing ? 'Indexing...' : `Index ${selectedItems.length} Selected Documents`}
          </button>
        </div>
      </div>

      {/* Selected Counter Notice */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '0.6rem 1rem',
          background: 'rgba(0, 120, 212, 0.08)',
          border: '1px solid rgba(0, 120, 212, 0.2)',
          borderRadius: 'var(--radius-md)',
          marginBottom: '1.25rem',
          fontSize: '0.8125rem',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: '#60a5fa' }}>
          <Sparkles size={14} />
          <span>
            <strong>{selectedItems.length} documents</strong> ready for metadata extraction, chunking, and pgvector embeddings.
          </span>
        </div>
        <button
          onClick={() => {
            const allSelected = selectedItems.length > 0;
            const updated: Record<string, boolean> = {};
            if (!allSelected) {
              for (const it of items) {
                if (!it.is_folder) updated[it.id] = true;
                if (it.children) it.children.forEach((c) => (updated[c.id] = true));
              }
            }
            setSelectedIds(updated);
          }}
          style={{ background: 'transparent', border: 'none', color: '#93c5fd', cursor: 'pointer', fontSize: '0.75rem' }}
        >
          {selectedItems.length > 0 ? 'Deselect All' : 'Select All'}
        </button>
      </div>

      {/* Tree Content */}
      <div
        style={{
          maxHeight: '520px',
          overflowY: 'auto',
          paddingRight: '0.5rem',
        }}
      >
        {loading ? (
          <div style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-muted)' }}>
            <RefreshCw size={24} className="animate-spin" style={{ margin: '0 auto 0.75rem' }} />
            <p>Loading OneDrive directory tree...</p>
          </div>
        ) : items.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-muted)' }}>
            <p>No OneDrive documents found.</p>
          </div>
        ) : (
          items.map((item) => renderItem(item))
        )}
      </div>
    </div>
  );
}
