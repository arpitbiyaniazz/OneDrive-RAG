import { useEffect, useRef, useState } from 'react';
import { RefreshCw, CheckCircle2, AlertCircle, Database, X } from 'lucide-react';
import { IngestionJob } from '../../types';
import { ApiError, fetchJob, isJobRunning } from '../../services/api';
import { useDialogFocus } from '../../hooks';

interface IngestionModalProps {
  jobId: string;
  onClose: () => void;
  /** Called once when the job finishes (successfully or not). */
  onComplete: () => void;
  /** "Go to Knowledge Base": show the indexed documents. */
  onViewDocuments: () => void;
}

const POLL_MS = 1200;
const MAX_POLL_DELAY_MS = 15000;
const MAX_POLL_FAILURES = 6;

export function IngestionModal({ jobId, onClose, onComplete, onViewDocuments }: IngestionModalProps) {
  const [job, setJob] = useState<IngestionJob | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const onCompleteRef = useRef(onComplete);
  const { ref, onKeyDown } = useDialogFocus<HTMLDivElement>(onClose);

  useEffect(() => {
    onCompleteRef.current = onComplete;
  });

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    setJob(null);
    setPollError(null);

    async function poll() {
      try {
        const data = await fetchJob(jobId);
        if (stopped) return;
        failures = 0;
        setJob(data);
        if (!isJobRunning(data.status)) {
          onCompleteRef.current();
          return;
        }
      } catch (e) {
        if (stopped) return;
        const status = e instanceof ApiError ? e.status : 0;
        // A client error (e.g. the job no longer exists) will not fix itself: stop polling.
        if (status >= 400 && status < 500 && status !== 408 && status !== 429) {
          setPollError((e as Error).message);
          return;
        }
        failures += 1;
        if (failures >= MAX_POLL_FAILURES) {
          setPollError(
            `Lost contact with the server (${(e as Error).message}). Indexing continues in the background; check the Knowledge Base later.`
          );
          return;
        }
      }
      if (stopped) return;
      // Back off while the server is unreachable.
      const delay = failures ? Math.min(POLL_MS * 2 ** failures, MAX_POLL_DELAY_MS) : POLL_MS;
      timer = setTimeout(poll, delay);
    }
    poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [jobId]);

  const finished = job !== null && !isJobRunning(job.status);
  const percent = finished ? 100 : Math.min(100, Math.max(0, job?.progress_percent ?? 0));
  const problems = (job?.result?.files || []).filter((f) => f.status === 'FAILED' || f.status === 'UNSUPPORTED');
  const canViewDocuments = job?.status === 'COMPLETED' || job?.status === 'COMPLETED_WITH_ERRORS';

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
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby="ingestion-modal-title"
        tabIndex={-1}
        onKeyDown={onKeyDown}
        className="glass-panel"
        style={{
          width: '100%',
          maxWidth: '520px',
          maxHeight: '100%',
          overflowY: 'auto',
          padding: 'clamp(1.25rem, 5vw, 2rem)',
          background: 'var(--app-surface)',
          boxShadow: '0 20px 50px rgba(0, 0, 0, 0.6)',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
            <Database size={20} color="#0078d4" />
            <h3 id="ingestion-modal-title" style={{ fontSize: '1.15rem', fontWeight: 700 }}>Indexing documents</h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            title="Close"
            style={{ background: 'transparent', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', display: 'flex' }}
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
            <span style={{ fontWeight: 700, color: 'var(--info-text)' }}>{percent}%</span>
          </div>

          <div
            role="progressbar"
            aria-label="Indexing progress"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={percent}
            style={{
              width: '100%',
              height: '10px',
              borderRadius: '5px',
              background: 'var(--app-border)',
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
              {job?.processed_files ?? 0} / {job?.total_files ?? 0}
            </p>
          </div>
          <div>
            <span style={{ color: 'var(--text-muted)' }}>Passages indexed:</span>
            <p style={{ fontWeight: 700, fontSize: '1.1rem', color: 'var(--success-text)', marginTop: '0.2rem' }}>
              {job?.total_chunks ?? 0}
            </p>
          </div>
        </div>

        <div role="status">
          {job?.status === 'COMPLETED' ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--success-text)', marginBottom: '1rem' }}>
              <CheckCircle2 size={18} />
              <span>{problems.length ? 'Finished. Some files could not be indexed:' : 'All selected documents are indexed and searchable.'}</span>
            </div>
          ) : job?.status === 'FAILED' || job?.status === 'COMPLETED_WITH_ERRORS' ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--danger-text)', marginBottom: '1rem' }}>
              <AlertCircle size={18} />
              <span>{job.status === 'FAILED' ? 'Indexing failed' : 'Finished with errors'}{job.error_message ? `: ${job.error_message}` : ''}</span>
            </div>
          ) : pollError ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--danger-text)', marginBottom: '1rem' }}>
              <AlertCircle size={18} style={{ flexShrink: 0 }} />
              <span>Could not get the indexing status: {pollError}</span>
            </div>
          ) : (
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--text-secondary)', marginBottom: '1.5rem' }}>
              <RefreshCw size={16} className="animate-spin" />
              <span>Downloading, reading and indexing your files...</span>
            </div>
          )}
        </div>

        {problems.length > 0 && (
          <ul style={{ maxHeight: '160px', overflowY: 'auto', fontSize: '0.8rem', color: 'var(--text-secondary)', marginBottom: '1.5rem', paddingLeft: '1.1rem' }}>
            {problems.map((f, i) => (
              <li key={i}>
                <strong>{f.name}</strong>: {f.reason || f.status.toLowerCase()}
              </li>
            ))}
          </ul>
        )}

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.75rem' }}>
          <button type="button" className="btn btn-primary" onClick={canViewDocuments ? onViewDocuments : onClose}>
            {canViewDocuments ? 'Go to Knowledge Base' : 'Close'}
          </button>
        </div>
      </div>
    </div>
  );
}
