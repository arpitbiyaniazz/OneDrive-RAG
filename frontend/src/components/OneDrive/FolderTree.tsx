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
import { OneDriveItem } from '../../types';

interface FolderTreeProps {
  onIndexSelection: (selectedIds: string[], selectedItems: OneDriveItem[]) => void;
  isIndexing?: boolean;
}

export function FolderTree({ onIndexSelection, isIndexing = false }: FolderTreeProps) {
  const [items, setItems] = useState<OneDriveItem[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [expandedFolders, setExpandedFolders] = useState<Record<string, boolean>>({
    folder_hr: true,
    folder_finance: true,
    folder_engineering: true,
  });
  const [selectedIds, setSelectedIds] = useState<Record<string, boolean>>({});
  const [searchQuery, setSearchQuery] = useState<string>('');

  useEffect(() => {
    fetchTree();
  }, []);

  async function fetchTree() {
    setLoading(true);
    try {
      const res = await fetch('/api/onedrive/tree');
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
      console.error('Failed to fetch OneDrive tree', e);
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
            onClick={fetchTree}
            disabled={loading}
            title="Refresh OneDrive items"
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
