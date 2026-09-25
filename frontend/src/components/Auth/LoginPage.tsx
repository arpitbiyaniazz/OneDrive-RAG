import { useState } from 'react';
import {
  FolderSync,
  Sparkles,
  ShieldCheck,
  Layers,
  ArrowRight,
  ExternalLink,
  Zap,
  CheckCircle2,
  AlertCircle,
  Cpu,
  FileSpreadsheet,
} from 'lucide-react';
import { HealthStatus, TelemetryStatus, UserProfile } from '../../types';
import { fetchLoginUrl, loginSandboxDemo } from '../../services/api';

interface LoginPageProps {
  onLoginSuccess: (user: UserProfile) => void;
  health: HealthStatus | null;
  telemetry: TelemetryStatus | null;
}

export function LoginPage({ onLoginSuccess, health, telemetry }: LoginPageProps) {
  const [loading, setLoading] = useState<'microsoft' | 'sandbox' | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  async function handleMicrosoftLogin() {
    try {
      setLoading('microsoft');
      setErrorMessage(null);
      const url = await fetchLoginUrl();
      if (url.startsWith('http://') || url.startsWith('https://')) {
        window.location.href = url;
      } else {
        // If relative URL (e.g. dev mock callback)
        window.location.href = url;
      }
    } catch (err: any) {
      setErrorMessage(err.message || 'Failed to initiate Microsoft OAuth login');
      setLoading(null);
    }
  }

  async function handleSandboxLogin() {
    try {
      setLoading('sandbox');
      setErrorMessage(null);
      const res = await loginSandboxDemo();
      onLoginSuccess(res.user);
    } catch (err: any) {
      setErrorMessage(err.message || 'Failed to launch sandbox demo session');
      setLoading(null);
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        alignItems: 'center',
        padding: '2rem 1.5rem',
        position: 'relative',
        overflow: 'hidden',
        background: 'radial-gradient(ellipse at 50% 10%, #172554 0%, #0a0d14 65%)',
      }}
    >
      {/* Decorative ambient background glows */}
      <div
        style={{
          position: 'absolute',
          top: '-10%',
          left: '20%',
          width: '500px',
          height: '500px',
          borderRadius: '50%',
          background: 'radial-gradient(circle, rgba(0, 120, 212, 0.15) 0%, transparent 70%)',
          pointerEvents: 'none',
          filter: 'blur(60px)',
        }}
      />
      <div
        style={{
          position: 'absolute',
          bottom: '-10%',
          right: '15%',
          width: '600px',
          height: '600px',
          borderRadius: '50%',
          background: 'radial-gradient(circle, rgba(139, 92, 246, 0.12) 0%, transparent 70%)',
          pointerEvents: 'none',
          filter: 'blur(70px)',
        }}
      />

      <div
        style={{
          width: '100%',
          maxWidth: '1080px',
          zIndex: 1,
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
          gap: '2.5rem',
          alignItems: 'center',
        }}
      >
        {/* Left Column: Brand & Architecture Highlights */}
        <div>
          <div style={{ display: 'inline-flex', alignItems: 'center', gap: '0.6rem', marginBottom: '1.25rem' }}>
            <div
              style={{
                width: '44px',
                height: '44px',
                borderRadius: '12px',
                background: 'linear-gradient(135deg, #0078D4 0%, #06B6D4 100%)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                boxShadow: '0 8px 24px rgba(0, 120, 212, 0.35)',
              }}
            >
              <FolderSync size={24} color="#ffffff" />
            </div>
            <span
              style={{
                fontSize: '0.85rem',
                fontWeight: 700,
                letterSpacing: '0.08em',
                textTransform: 'uppercase',
                color: '#60a5fa',
              }}
            >
              Enterprise Document Intelligence
            </span>
          </div>

          <h1
            style={{
              fontSize: '2.5rem',
              fontWeight: 800,
              letterSpacing: '-0.03em',
              lineHeight: 1.15,
              marginBottom: '1rem',
              background: 'linear-gradient(135deg, #ffffff 0%, #cbd5e1 50%, #94a3b8 100%)',
              WebkitBackgroundClip: 'text',
              WebkitTextFillColor: 'transparent',
            }}
          >
            OneDrive RAG <br />
            Knowledge Assistant
          </h1>

          <p
            style={{
              fontSize: '1rem',
              color: 'var(--text-secondary)',
              lineHeight: 1.6,
              marginBottom: '2rem',
              maxWidth: '480px',
            }}
          >
            Ground your enterprise LLM in company OneDrive files (PDF, DOCX, XLSX, PPTX) with pgvector, delta-sync, and dual-branch telemetry.
          </p>

          {/* Value Highlights */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0.85rem' }}>
              <div
                style={{
                  width: '32px',
                  height: '32px',
                  borderRadius: '8px',
                  background: 'rgba(0, 120, 212, 0.15)',
                  border: '1px solid rgba(0, 120, 212, 0.3)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  flexShrink: 0,
                }}
              >
                <ShieldCheck size={18} color="#38bdf8" />
              </div>
              <div>
                <h4 style={{ fontSize: '0.925rem', fontWeight: 600, color: '#f1f5f9' }}>
                  Azure AD Multi-Tenant Isolation
                </h4>
                <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                  Per-user encrypted tokens with strict SQL metadata row filtering and Microsoft Graph delta sync.
                </p>
              </div>
            </div>

            <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0.85rem' }}>
              <div
                style={{
                  width: '32px',
                  height: '32px',
                  borderRadius: '8px',
                  background: 'rgba(139, 92, 246, 0.15)',
                  border: '1px solid rgba(139, 92, 246, 0.3)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  flexShrink: 0,
                }}
              >
                <Layers size={18} color="#c084fc" />
              </div>
              <div>
                <h4 style={{ fontSize: '0.925rem', fontWeight: 600, color: '#f1f5f9' }}>
                  Dual-Branch Observability
                </h4>
                <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                  OpenTelemetry distributed system spans seamlessly cross-correlated with Langfuse LLM/RAG evals.
                </p>
              </div>
            </div>

            <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0.85rem' }}>
              <div
                style={{
                  width: '32px',
                  height: '32px',
                  borderRadius: '8px',
                  background: 'rgba(16, 185, 129, 0.15)',
                  border: '1px solid rgba(16, 185, 129, 0.3)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  flexShrink: 0,
                }}
              >
                <FileSpreadsheet size={18} color="#34d399" />
              </div>
              <div>
                <h4 style={{ fontSize: '0.925rem', fontWeight: 600, color: '#f1f5f9' }}>
                  Multi-Format Structured Extraction
                </h4>
                <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                  Preserves complex Excel tables, PowerPoint slide decks, Word headings, and PDF page numbers.
                </p>
              </div>
            </div>
          </div>
        </div>

        {/* Right Column: Glassmorphic Login Card */}
        <div
          className="glass-panel"
          style={{
            padding: '2.5rem',
            maxWidth: '460px',
            margin: '0 auto',
            width: '100%',
            border: '1px solid rgba(255, 255, 255, 0.12)',
            boxShadow: '0 20px 50px rgba(0, 0, 0, 0.6), 0 0 40px rgba(0, 120, 212, 0.15)',
          }}
        >
          <div style={{ textAlign: 'center', marginBottom: '2rem' }}>
            <h2 style={{ fontSize: '1.4rem', fontWeight: 700, marginBottom: '0.4rem', color: '#ffffff' }}>
              Sign In to Your Workspace
            </h2>
            <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
              Select your authentication method to access indexed OneDrive repositories.
            </p>
          </div>

          {errorMessage && (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.6rem',
                padding: '0.75rem 1rem',
                borderRadius: 'var(--radius-md)',
                background: 'rgba(244, 63, 94, 0.15)',
                border: '1px solid rgba(244, 63, 94, 0.3)',
                color: '#fb7185',
                fontSize: '0.825rem',
                marginBottom: '1.5rem',
              }}
            >
              <AlertCircle size={16} />
              <span>{errorMessage}</span>
            </div>
          )}

          {/* Primary Action: Microsoft OAuth */}
          <button
            onClick={handleMicrosoftLogin}
            disabled={loading !== null}
            style={{
              width: '100%',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '0.85rem',
              padding: '0.9rem 1.25rem',
              borderRadius: 'var(--radius-md)',
              background: '#2f2f2f',
              border: '1px solid rgba(255, 255, 255, 0.2)',
              color: '#ffffff',
              fontSize: '0.95rem',
              fontWeight: 600,
              cursor: loading ? 'not-allowed' : 'pointer',
              transition: 'all 0.2s ease',
              boxShadow: '0 4px 12px rgba(0, 0, 0, 0.3)',
              marginBottom: '1.25rem',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = '#3c3c3c';
              e.currentTarget.style.borderColor = 'rgba(255, 255, 255, 0.4)';
              e.currentTarget.style.transform = 'translateY(-1px)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = '#2f2f2f';
              e.currentTarget.style.borderColor = 'rgba(255, 255, 255, 0.2)';
              e.currentTarget.style.transform = 'translateY(0)';
            }}
          >
            {/* Microsoft 4-Color Logo */}
            <svg width="20" height="20" viewBox="0 0 21 21" fill="none" xmlns="http://www.w3.org/2000/svg">
              <rect x="1" y="1" width="9" height="9" fill="#f25022" />
              <rect x="11" y="1" width="9" height="9" fill="#7fba00" />
              <rect x="1" y="11" width="9" height="9" fill="#00a4ef" />
              <rect x="11" y="11" width="9" height="9" fill="#ffb900" />
            </svg>
            <span>
              {loading === 'microsoft' ? 'Redirecting to Microsoft...' : 'Sign In with Microsoft 365'}
            </span>
            <ExternalLink size={15} style={{ opacity: 0.6, marginLeft: 'auto' }} />
          </button>

          {/* Divider */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '1rem',
              margin: '1.5rem 0',
              color: 'var(--text-muted)',
              fontSize: '0.75rem',
              textTransform: 'uppercase',
              letterSpacing: '0.05em',
            }}
          >
            <div style={{ flex: 1, height: '1px', background: 'var(--border-color)' }} />
            <span>OR TEST INSTANTLY</span>
            <div style={{ flex: 1, height: '1px', background: 'var(--border-color)' }} />
          </div>

          {/* Sandbox Demo Bypass Button */}
          <button
            onClick={handleSandboxLogin}
            disabled={loading !== null}
            className="btn btn-primary"
            style={{
              width: '100%',
              padding: '0.9rem 1.25rem',
              fontSize: '0.925rem',
              justifyContent: 'center',
              background: 'linear-gradient(135deg, #0284c7 0%, #0078d4 100%)',
              border: '1px solid rgba(56, 189, 248, 0.4)',
              cursor: loading ? 'not-allowed' : 'pointer',
            }}
          >
            <Zap size={18} />
            <span>
              {loading === 'sandbox' ? 'Preparing Demo Workspace...' : 'Launch Interactive Demo Sandbox'}
            </span>
            <ArrowRight size={16} style={{ marginLeft: 'auto' }} />
          </button>

          <p
            style={{
              fontSize: '0.75rem',
              color: 'var(--text-muted)',
              textAlign: 'center',
              marginTop: '0.75rem',
              lineHeight: 1.4,
            }}
          >
            Pre-loaded with financial tables, engineering blueprints, company HR policies, and marketing decks.
          </p>

          {/* Live System Diagnostics */}
          <div
            style={{
              marginTop: '2rem',
              paddingTop: '1.25rem',
              borderTop: '1px solid var(--border-color)',
              display: 'flex',
              flexDirection: 'column',
              gap: '0.5rem',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '0.75rem' }}>
              <span style={{ color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                <Cpu size={13} /> Backend Service:
              </span>
              <span style={{ color: health ? '#34d399' : '#f87171', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                {health ? <CheckCircle2 size={12} /> : <AlertCircle size={12} />}
                {health ? `Online (v${health.version})` : 'Offline'}
              </span>
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '0.75rem' }}>
              <span style={{ color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                <Layers size={13} /> OpenTelemetry:
              </span>
              <span style={{ color: telemetry?.opentelemetry.tracer_active ? '#a78bfa' : '#94a3b8', fontWeight: 600 }}>
                {telemetry?.opentelemetry.tracer_active ? 'Active (localhost:4317)' : 'Ready'}
              </span>
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '0.75rem' }}>
              <span style={{ color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                <Sparkles size={13} /> Langfuse Evals:
              </span>
              <span style={{ color: telemetry?.langfuse.client_active ? '#22d3ee' : '#94a3b8', fontWeight: 600 }}>
                {telemetry?.langfuse.client_active ? 'Instrumented' : 'Configured'}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Footer disclaimer */}
      <footer
        style={{
          marginTop: '3rem',
          fontSize: '0.75rem',
          color: 'var(--text-muted)',
          textAlign: 'center',
          zIndex: 1,
        }}
      >
        Enterprise Microsoft Graph RAG Architecture &bull; PostgreSQL pgvector &bull; OpenTelemetry &bull; Langfuse Observability
      </footer>
    </div>
  );
}
