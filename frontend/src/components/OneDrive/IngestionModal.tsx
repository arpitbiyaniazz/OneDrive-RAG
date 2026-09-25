import { useEffect, useState } from 'react';
import { RefreshCw, CheckCircle2, AlertCircle, Database, X } from 'lucide-react';
import { IngestionJob } from '../../types';
import { getAuthHeaders } from '../../services/api';

interface IngestionModalProps {
  jobId: string;
  onClose: () => void;
  onComplete: () => void;
}

export function IngestionModal({ jobId, onClose, onComplete }: IngestionModalProps) {
  const [job, setJob] = useState<IngestionJob | null>(null);

  useEffect(() => {
    let interval: any;
    async function poll() {
      try {
        const res = await fetch(`/api/ingestion/${jobId}`, { headers: getAuthHeaders() });
        if (res.ok) {
          const data = await res.json();
          setJob(data);
          if (data.status === 'COMPLETED' || data.status === 'FAILED' || data.status === 'COMPLETED_WITH_ERRORS') {
            clearInterval(interval);
            onComplete();
          }
        }
      } catch (e) {
        console.error('Failed to poll ingestion job', e);
      }
    }

    poll();
    interval = setInterval(poll, 1000);
    return () => clearInterval(interval);
  }, [jobId]);

  const percent = job?.total_files
    ? Math.round((job.processed_files / job.total_files) * 100)
    : 0;

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(0, 0, 0, 0.75)',
        backdropFilter: 'blur(8px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 100,
        padding: '1.5rem',
      }}
    >
      <div
        className="glass-panel"
        style={{
          width: '100%',
          maxWidth: '520px',
          padding: '2rem',
          background: 'rgba(15, 23, 42, 0.95)',
          boxShadow: '0 20px 50px rgba(0, 0, 0, 0.6)',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
            <Database size={20} color="#0078d4" />
            <h3 style={{ fontSize: '1.15rem', fontWeight: 700 }}>Ingesting OneDrive Documents</h3>
          </div>
          <button
            onClick={onClose}
            style={{ background: 'transparent', border: 'none', color: 'var(--text-muted)', cursor: 'pointer' }}
          >
            <X size={18} />
          </button>
        </div>

        {/* Progress Bar */}
        <div style={{ marginBottom: '1.5rem' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem', fontSize: '0.875rem' }}>
            <span style={{ color: 'var(--text-secondary)' }}>
              Status: <strong>{job?.status || 'INITIALIZING'}</strong>
            </span>
            <span style={{ fontWeight: 700, color: '#38bdf8' }}>{percent}%</span>
          </div>

          <div
            style={{
              width: '100%',
              height: '10px',
              borderRadius: '5px',
              background: 'rgba(255, 255, 255, 0.08)',
              overflow: 'hidden',
            }}
          >
            <div
              style={{
                width: `${percent}%`,
                height: '100%',
                background: 'linear-gradient(90deg, #0078d4 0%, #06b6d4 100%)',
                transition: 'width 0.3s ease',
              }}
            />
          </div>
        </div>

        {/* Details Grid */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            gap: '1rem',
            marginBottom: '1.5rem',
            background: 'rgba(0, 0, 0, 0.25)',
            padding: '1rem',
            borderRadius: 'var(--radius-md)',
            fontSize: '0.8125rem',
          }}
        >
          <div>
            <span style={{ color: 'var(--text-muted)' }}>Files Processed:</span>
            <p style={{ fontWeight: 700, fontSize: '1.1rem', marginTop: '0.2rem' }}>
              {job?.processed_files || 0} / {job?.total_files || 0}
            </p>
          </div>
          <div>
            <span style={{ color: 'var(--text-muted)' }}>pgvector Chunks:</span>
            <p style={{ fontWeight: 700, fontSize: '1.1rem', color: '#10b981', marginTop: '0.2rem' }}>
              {job?.total_chunks || 0}
            </p>
          </div>
        </div>

        {job?.status === 'COMPLETED' ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: '#34d399', marginBottom: '1.5rem' }}>
            <CheckCircle2 size={18} />
            <span>All documents successfully parsed, embedded, and indexed!</span>
          </div>
        ) : job?.status === 'FAILED' ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: '#fb7185', marginBottom: '1.5rem' }}>
            <AlertCircle size={18} />
            <span>Ingestion failed: {job.error_message || 'Unknown error'}</span>
          </div>
        ) : (
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--text-secondary)', marginBottom: '1.5rem' }}>
            <RefreshCw size={16} className="animate-spin" />
            <span>Extracting metadata, creating chunks, and generating embeddings...</span>
          </div>
        )}

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.75rem' }}>
          <button className="btn btn-primary" onClick={onClose}>
            {job?.status === 'COMPLETED' ? 'Go to Knowledge Base' : 'Close'}
          </button>
        </div>
      </div>
    </div>
  );
}
