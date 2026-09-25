import { useEffect, useState } from 'react';
import {
  Activity,
  Layers,
  Sparkles,
  FolderSync,
  Bot,
  Database,
  CheckCircle2,
  AlertCircle,
  ExternalLink,
  ShieldCheck,
  Zap,
} from 'lucide-react';
import { HealthStatus, TelemetryStatus, OneDriveItem } from './types';
import { fetchHealth, fetchTelemetryStatus } from './services/api';
import { FolderTree } from './components/OneDrive/FolderTree';
import { IngestionModal } from './components/OneDrive/IngestionModal';
import { ChatBox } from './components/Chat/ChatBox';
import { KnowledgeBaseTable } from './components/Dashboard/KnowledgeBaseTable';

export function App() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [telemetry, setTelemetry] = useState<TelemetryStatus | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'overview' | 'onedrive' | 'knowledge' | 'chat'>('overview');
  const [activeJobId, setActiveJobId] = useState<string | null>(null);

  useEffect(() => {
    async function loadData() {
      try {
        setLoading(true);
        const [h, t] = await Promise.all([fetchHealth(), fetchTelemetryStatus()]);
        setHealth(h);
        setTelemetry(t);
        setError(null);
      } catch (err: any) {
        setError(err.message || 'Could not connect to backend');
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, []);

  async function handleIndexSelection(selectedIds: string[], _items: OneDriveItem[]) {
    try {
      const res = await fetch('/api/ingestion/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ item_ids: selectedIds, folder_path: '/Company' }),
      });
      if (res.ok) {
        const data = await res.json();
        setActiveJobId(data.job_id);
      }
    } catch (e) {
      console.error('Failed to trigger ingestion', e);
    }
  }

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
      {/* Top Header */}
      <header
        style={{
          borderBottom: '1px solid var(--border-color)',
          background: 'rgba(10, 13, 20, 0.8)',
          backdropFilter: 'blur(12px)',
          position: 'sticky',
          top: 0,
          zIndex: 50,
        }}
      >
        <div
          style={{
            maxWidth: '1280px',
            margin: '0 auto',
            padding: '1rem 1.5rem',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <div
              style={{
                width: '40px',
                height: '40px',
                borderRadius: '10px',
                background: 'linear-gradient(135deg, #0078D4 0%, #06B6D4 100%)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                boxShadow: '0 4px 14px rgba(0, 120, 212, 0.4)',
              }}
            >
              <FolderSync size={22} color="#ffffff" />
            </div>
            <div>
              <h1 style={{ fontSize: '1.15rem', fontWeight: 700, letterSpacing: '-0.02em' }}>
                OneDrive RAG Assistant
              </h1>
              <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
                Metadata-Aware Enterprise Intelligence
              </span>
            </div>
          </div>

          {/* Status Badges */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            {loading ? (
              <span className="badge badge-info">
                <Activity size={12} className="animate-spin" /> Connecting...
              </span>
            ) : error ? (
              <span className="badge" style={{ background: 'rgba(244,63,94,0.15)', color: '#fb7185', border: '1px solid rgba(244,63,94,0.3)' }}>
                <AlertCircle size={12} /> Backend Offline
              </span>
            ) : (
              <>
                <span className="badge badge-success">
                  <CheckCircle2 size={12} /> Backend v{health?.version}
                </span>
                <span className="badge badge-purple">
                  <Layers size={12} /> OTel Active
                </span>
                <span className="badge badge-info">
                  <Sparkles size={12} /> Langfuse Ready
                </span>
              </>
            )}
          </div>
        </div>
      </header>

      {/* Main Content Area */}
      <main style={{ flex: 1, maxWidth: '1280px', width: '100%', margin: '0 auto', padding: '2rem 1.5rem' }}>
        {/* Navigation Tabs */}
        <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '2rem', borderBottom: '1px solid var(--border-color)', paddingBottom: '0.75rem' }}>
          {[
            { id: 'overview', label: 'System Overview & Telemetry', icon: Layers },
            { id: 'onedrive', label: 'OneDrive Browser', icon: FolderSync },
            { id: 'knowledge', label: 'Knowledge Base', icon: Database },
            { id: 'chat', label: 'Grounded Chat', icon: Bot },
          ].map((tab) => {
            const Icon = tab.icon;
            const active = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id as any)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.5rem',
                  padding: '0.6rem 1.1rem',
                  borderRadius: 'var(--radius-md)',
                  background: active ? 'rgba(0, 120, 212, 0.15)' : 'transparent',
                  color: active ? '#60a5fa' : 'var(--text-secondary)',
                  border: active ? '1px solid rgba(0, 120, 212, 0.3)' : '1px solid transparent',
                  fontWeight: 600,
                  fontSize: '0.875rem',
                  cursor: 'pointer',
                  transition: 'all 0.15s ease',
                }}
              >
                <Icon size={16} />
                {tab.label}
              </button>
            );
          })}
        </div>

        {/* Tab Content */}
        {activeTab === 'overview' && (
          <div>
            {/* Hero Banner */}
            <div
              className="glass-panel"
              style={{
                padding: '2.5rem',
                marginBottom: '2rem',
                position: 'relative',
                overflow: 'hidden',
                background: 'linear-gradient(135deg, rgba(22, 28, 45, 0.9) 0%, rgba(15, 23, 42, 0.95) 100%)',
              }}
            >
              <div style={{ maxWidth: '720px' }}>
                <div style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1rem' }}>
                  <span className="badge badge-info">Phases 1–6 Operational</span>
                  <span className="badge badge-success">Grounded RAG Live</span>
                </div>
                <h2 style={{ fontSize: '2rem', fontWeight: 800, marginBottom: '0.75rem', letterSpacing: '-0.02em' }}>
                  OneDrive Metadata-Aware RAG with Dual Telemetry
                </h2>
                <p style={{ color: 'var(--text-secondary)', fontSize: '1rem', lineHeight: '1.6', marginBottom: '1.5rem' }}>
                  Explore your OneDrive folders, index multi-format documents (PDF, DOCX, XLSX, PPTX, TXT) into PostgreSQL pgvector, and ask questions with verified source citations linking directly to your OneDrive files.
                </p>
                <div style={{ display: 'flex', gap: '1rem' }}>
                  <button className="btn btn-primary" onClick={() => setActiveTab('onedrive')}>
                    <FolderSync size={16} /> Browse & Index OneDrive
                  </button>
                  <button className="btn btn-secondary" onClick={() => setActiveTab('chat')}>
                    <Bot size={16} /> Open Grounded Chat
                  </button>
                  <a
                    href="http://localhost:8000/docs"
                    target="_blank"
                    rel="noreferrer"
                    className="btn btn-secondary"
                  >
                    API Swagger <ExternalLink size={14} />
                  </a>
                </div>
              </div>
            </div>

            {/* Dual Observability Architecture Grid */}
            <h3 style={{ fontSize: '1.25rem', fontWeight: 700, marginBottom: '1rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <Zap size={18} color="#06b6d4" />
              Dual-Branch Observability Architecture
            </h3>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(350px, 1fr))', gap: '1.5rem', marginBottom: '2rem' }}>
              {/* Branch 1: OpenTelemetry */}
              <div className="glass-card" style={{ padding: '1.75rem' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '1rem' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                    <div style={{ padding: '0.5rem', borderRadius: '8px', background: 'rgba(139, 92, 246, 0.15)' }}>
                      <Layers size={22} color="#a78bfa" />
                    </div>
                    <div>
                      <h4 style={{ fontSize: '1.1rem', fontWeight: 700 }}>OpenTelemetry</h4>
                      <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>System & Infrastructure Telemetry</span>
                    </div>
                  </div>
                  <span className="badge badge-purple">Active</span>
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', fontSize: '0.875rem' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0.5rem 0', borderBottom: '1px solid var(--border-color)' }}>
                    <span style={{ color: 'var(--text-secondary)' }}>API Traces (FastAPI)</span>
                    <span style={{ color: '#34d399', fontWeight: 600 }}>Instrumented</span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0.5rem 0', borderBottom: '1px solid var(--border-color)' }}>
                    <span style={{ color: 'var(--text-secondary)' }}>DB Latency (SQLAlchemy + pgvector)</span>
                    <span style={{ color: '#34d399', fontWeight: 600 }}>Instrumented</span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0.5rem 0', borderBottom: '1px solid var(--border-color)' }}>
                    <span style={{ color: 'var(--text-secondary)' }}>OneDrive API Calls (HTTPX Graph)</span>
                    <span style={{ color: '#34d399', fontWeight: 600 }}>Instrumented</span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0.5rem 0', borderBottom: '1px solid var(--border-color)' }}>
                    <span style={{ color: 'var(--text-secondary)' }}>Worker Latency (Parsers/Chunking)</span>
                    <span style={{ color: '#34d399', fontWeight: 600 }}>@trace_worker</span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0.5rem 0' }}>
                    <span style={{ color: 'var(--text-secondary)' }}>Service Name</span>
                    <span style={{ color: '#38bdf8', fontFamily: 'var(--font-mono)' }}>
                      {telemetry?.opentelemetry.service_name || 'onedrive-rag-backend'}
                    </span>
                  </div>
                </div>
              </div>

              {/* Branch 2: Langfuse */}
              <div className="glass-card" style={{ padding: '1.75rem' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '1rem' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                    <div style={{ padding: '0.5rem', borderRadius: '8px', background: 'rgba(6, 182, 212, 0.15)' }}>
                      <Sparkles size={22} color="#22d3ee" />
                    </div>
                    <div>
                      <h4 style={{ fontSize: '1.1rem', fontWeight: 700 }}>Langfuse</h4>
                      <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>LLM, RAG & Evals Telemetry</span>
                    </div>
                  </div>
                  <span className="badge badge-info">
                    {telemetry?.langfuse.configured ? 'Connected' : 'Ready'}
                  </span>
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', fontSize: '0.875rem' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0.5rem 0', borderBottom: '1px solid var(--border-color)' }}>
                    <span style={{ color: 'var(--text-secondary)' }}>LLM & Generation Traces</span>
                    <span style={{ color: '#22d3ee', fontWeight: 600 }}>@observe_rag</span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0.5rem 0', borderBottom: '1px solid var(--border-color)' }}>
                    <span style={{ color: 'var(--text-secondary)' }}>RAG Candidate Chunks & Scores</span>
                    <span style={{ color: '#34d399', fontWeight: 600 }}>Captured</span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0.5rem 0', borderBottom: '1px solid var(--border-color)' }}>
                    <span style={{ color: 'var(--text-secondary)' }}>Token Usage & Cost Tracking</span>
                    <span style={{ color: '#34d399', fontWeight: 600 }}>Real-time USD</span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0.5rem 0', borderBottom: '1px solid var(--border-color)' }}>
                    <span style={{ color: 'var(--text-secondary)' }}>Automated Evals & Groundedness</span>
                    <span style={{ color: '#34d399', fontWeight: 600 }}>LLM-as-a-judge</span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0.5rem 0' }}>
                    <span style={{ color: 'var(--text-secondary)' }}>Host Target</span>
                    <span style={{ color: '#a78bfa', fontFamily: 'var(--font-mono)' }}>
                      {telemetry?.langfuse.host || 'cloud.langfuse.com'}
                    </span>
                  </div>
                </div>
              </div>
            </div>

            {/* Architecture Details */}
            <div className="glass-panel" style={{ padding: '1.75rem' }}>
              <h4 style={{ fontSize: '1.05rem', fontWeight: 700, marginBottom: '1rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <ShieldCheck size={18} color="#10b981" />
                Security & Grounding Guarantees
              </h4>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '1rem' }}>
                <div style={{ padding: '1rem', background: 'rgba(255,255,255,0.02)', borderRadius: '8px', border: '1px solid var(--border-color)' }}>
                  <strong style={{ color: '#38bdf8' }}>Strict Multi-Tenant Isolation</strong>
                  <p style={{ fontSize: '0.825rem', color: 'var(--text-secondary)', marginTop: '0.25rem' }}>
                    Every pgvector query enforces user ID checks at the database layer. No user can retrieve cross-tenant data.
                  </p>
                </div>
                <div style={{ padding: '1rem', background: 'rgba(255,255,255,0.02)', borderRadius: '8px', border: '1px solid var(--border-color)' }}>
                  <strong style={{ color: '#34d399' }}>Prompt Injection Quarantine</strong>
                  <p style={{ fontSize: '0.825rem', color: 'var(--text-secondary)', marginTop: '0.25rem' }}>
                    All document excerpts are quarantined in untrusted tags. Instructions inside documents are never executed.
                  </p>
                </div>
                <div style={{ padding: '1rem', background: 'rgba(255,255,255,0.02)', borderRadius: '8px', border: '1px solid var(--border-color)' }}>
                  <strong style={{ color: '#a78bfa' }}>Verified Citations</strong>
                  <p style={{ fontSize: '0.825rem', color: 'var(--text-secondary)', marginTop: '0.25rem' }}>
                    Every answer is grounded in retrieved chunks and provides direct clickable links back to original OneDrive documents.
                  </p>
                </div>
              </div>
            </div>
          </div>
        )}

        {activeTab === 'onedrive' && (
          <div>
            <FolderTree onIndexSelection={handleIndexSelection} isIndexing={Boolean(activeJobId)} />
          </div>
        )}

        {activeTab === 'knowledge' && (
          <div>
            <KnowledgeBaseTable />
          </div>
        )}

        {activeTab === 'chat' && (
          <div>
            <ChatBox />
          </div>
        )}
      </main>

      {/* Ingestion Progress Modal */}
      {activeJobId && (
        <IngestionModal
          jobId={activeJobId}
          onClose={() => setActiveJobId(null)}
          onComplete={() => {
            // Optional callback
          }}
        />
      )}

      {/* Footer */}
      <footer
        style={{
          borderTop: '1px solid var(--border-color)',
          padding: '1.25rem 1.5rem',
          textAlign: 'center',
          fontSize: '0.8125rem',
          color: 'var(--text-muted)',
          background: 'rgba(10, 13, 20, 0.95)',
        }}
      >
        OneDrive Metadata-Aware RAG Chatbot • Production Architecture with OpenTelemetry & Langfuse
      </footer>
    </div>
  );
}
export default App;
