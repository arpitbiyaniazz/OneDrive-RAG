import { useEffect, useState } from 'react';
import {
  FolderSync,
  ShieldCheck,
  RefreshCw,
  ArrowRight,
  ExternalLink,
  Zap,
  CheckCircle2,
  AlertCircle,
  Cpu,
  FileSpreadsheet,
  Sun,
  Moon,
} from 'lucide-react';
import { HealthStatus, UserProfile } from '../../types';
import { loginSandbox, startOAuth } from '../../services/api';

interface LoginPageProps {
  onLoginSuccess: (user: UserProfile) => void;
  health: HealthStatus | null;
  initialError?: string | null;
  theme?: 'dark' | 'light';
  onToggleTheme?: () => void;
}

export function LoginPage({
  onLoginSuccess,
  health,
  initialError = null,
  theme = 'dark',
  onToggleTheme,
}: LoginPageProps) {
  const [loading, setLoading] = useState<'google' | 'sandbox' | null>(null); // 'microsoft': sign-in turned off
  const [errorMessage, setErrorMessage] = useState<string | null>(initialError);

  // e.g. "session expired" arriving while this page is already shown
  useEffect(() => {
    setErrorMessage(initialError);
  }, [initialError]);

  async function handleOAuth(provider: 'google') { // 'microsoft': sign-in turned off
    try {
      setLoading(provider);
      setErrorMessage(null);
      await startOAuth(provider);
    } catch (err) {
      setErrorMessage((err as Error).message || 'Sign-in could not be started');
      setLoading(null);
    }
  }

  // const handleMicrosoftLogin = () => handleOAuth('microsoft');  // Microsoft sign-in is turned off
  const handleGoogleLogin = () => handleOAuth('google');

  async function handleSandboxLogin() {
    try {
      setLoading('sandbox');
      setErrorMessage(null);
      onLoginSuccess(await loginSandbox());
    } catch (err) {
      setErrorMessage((err as Error).message || 'Failed to open the sandbox');
      setLoading(null);
    }
  }

  return (
    // Scrolls on small screens. Vertical centring uses auto margins on the content (not
    // justify-content), so content taller than the screen is never cut off at the top.
    <div
      style={{
        height: '100%',
        width: '100%',
        overflowY: 'auto',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        padding: '4rem 1.25rem 2rem',
        position: 'relative',
        background: 'var(--login-bg, radial-gradient(ellipse at 50% 10%, #172554 0%, #0a0d14 65%))',
      }}
    >
      {/* Top right Theme Switcher */}
      {onToggleTheme && (
        <div style={{ position: 'absolute', top: '1.25rem', right: '1.5rem', zIndex: 50 }}>
          <button
            onClick={onToggleTheme}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.45rem',
              padding: '0.45rem 0.85rem',
              background: 'var(--app-surface)',
              border: '1px solid var(--app-border)',
              borderRadius: 'var(--radius-full)',
              color: 'var(--app-text)',
              fontSize: '0.8rem',
              fontWeight: 600,
              cursor: 'pointer',
              transition: 'all 0.15s ease',
              boxShadow: 'var(--card-shadow)',
            }}
            title={`Switch to ${theme === 'dark' ? 'Light' : 'Dark'} mode`}
          >
            {theme === 'dark' ? (
              <>
                <Sun size={15} color="#f59e0b" />
                <span>Light</span>
              </>
            ) : (
              <>
                <Moon size={15} color="#0284c7" />
                <span>Dark</span>
              </>
            )}
          </button>
        </div>
      )}
      {/* Decorative ambient background glows (clipped so they never add scrollbars) */}
      <div aria-hidden="true" style={{ position: 'absolute', inset: 0, overflow: 'hidden', pointerEvents: 'none' }}>
        <div
          style={{
            position: 'absolute',
            top: '-10%',
            left: '20%',
            width: '500px',
            height: '500px',
            borderRadius: '50%',
            background: 'radial-gradient(circle, rgba(0, 120, 212, 0.15) 0%, transparent 70%)',
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
            filter: 'blur(70px)',
          }}
        />
      </div>

      <div
        style={{
          marginTop: 'auto',
          width: '100%',
          maxWidth: '1080px',
          zIndex: 1,
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(min(320px, 100%), 1fr))',
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
                color: 'var(--info-text)',
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
              background: 'var(--login-hero-title, linear-gradient(135deg, #ffffff 0%, #cbd5e1 50%, #94a3b8 100%))',
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
            Ask questions about your OneDrive and Google Drive files. Answers come only from the documents you choose to index, with inline citations that link back to the source.
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
                <h4 style={{ fontSize: '0.925rem', fontWeight: 600, color: 'var(--app-text)' }}>
                  Private to Your Account
                </h4>
                <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                  Every search is limited to the documents you indexed, and your drive access tokens are stored encrypted.
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
                <FileSpreadsheet size={18} color="#c084fc" />
              </div>
              <div>
                <h4 style={{ fontSize: '0.925rem', fontWeight: 600, color: 'var(--app-text)' }}>
                  Structure-Aware Extraction
                </h4>
                <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                  Keeps Word headings and tables, Excel sheets, PowerPoint slides and PDF page numbers, so each citation points to the right place.
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
                <RefreshCw size={18} color="#34d399" />
              </div>
              <div>
                <h4 style={{ fontSize: '0.925rem', fontWeight: 600, color: 'var(--app-text)' }}>
                  Folders Stay in Sync
                </h4>
                <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                  Each time you sync, changed files are re-indexed, deleted files are removed and new files in your selected folders are added.
                </p>
              </div>
            </div>
          </div>
        </div>

        {/* Right Column: Glassmorphic Login Card */}
        <div
          className="glass-panel"
          style={{
            padding: 'clamp(1.5rem, 6vw, 2.5rem)',
            maxWidth: '460px',
            margin: '0 auto',
            width: '100%',
            border: '1px solid rgba(255, 255, 255, 0.12)',
            boxShadow: '0 20px 50px rgba(0, 0, 0, 0.6), 0 0 40px rgba(0, 120, 212, 0.15)',
          }}
        >
          <div style={{ textAlign: 'center', marginBottom: '2rem' }}>
            <h2 style={{ fontSize: '1.4rem', fontWeight: 700, marginBottom: '0.4rem', color: 'var(--app-text)' }}>
              Sign In to Your Workspace
            </h2>
            <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
              Sign in with your Google account.
            </p>
          </div>

          {errorMessage && (
            <div
              role="alert"
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.6rem',
                padding: '0.75rem 1rem',
                borderRadius: 'var(--radius-md)',
                background: 'rgba(244, 63, 94, 0.15)',
                border: '1px solid rgba(244, 63, 94, 0.3)',
                color: 'var(--danger-text)',
                fontSize: '0.825rem',
                marginBottom: '1.5rem',
              }}
            >
              <AlertCircle size={16} style={{ flexShrink: 0 }} />
              <span style={{ overflowWrap: 'anywhere' }}>{errorMessage}</span>
            </div>
          )}

          {/* Microsoft sign-in is turned off: only Google sign-in is used.
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
            Microsoft 4-Color Logo
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
          */}

          {/* Primary Action: Google OAuth */}
          <button
            onClick={handleGoogleLogin}
            disabled={loading !== null}
            style={{
              width: '100%',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '0.85rem',
              padding: '0.9rem 1.25rem',
              borderRadius: 'var(--radius-md)',
              background: '#20242e',
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
              e.currentTarget.style.background = '#282e3c';
              e.currentTarget.style.borderColor = 'rgba(255, 255, 255, 0.4)';
              e.currentTarget.style.transform = 'translateY(-1px)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = '#20242e';
              e.currentTarget.style.borderColor = 'rgba(255, 255, 255, 0.2)';
              e.currentTarget.style.transform = 'translateY(0)';
            }}
          >
            {/* Google 4-Color Logo */}
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
              <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
              <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
              <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"/>
              <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"/>
            </svg>
            <span>
              {loading === 'google' ? 'Redirecting to Google...' : 'Sign In with Google Drive'}
            </span>
            <ExternalLink size={15} style={{ opacity: 0.6, marginLeft: 'auto' }} />
          </button>

          {/* Divider */}
          {health?.sandbox_mode && (
            <>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '1rem',
              margin: '1.25rem 0',
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
            Sandbox drives with sample PDFs, Word documents, spreadsheets and slides (development only).
          </p>

            </>
          )}

          {/* Service status */}
          <div
            style={{
              marginTop: '2rem',
              paddingTop: '1.25rem',
              borderTop: '1px solid var(--border-color)',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              fontSize: '0.75rem',
            }}
          >
            <span style={{ color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
              <Cpu size={13} /> Service status:
            </span>
            <span style={{ color: health ? 'var(--success-text)' : 'var(--danger-text)', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
              {health ? <CheckCircle2 size={12} /> : <AlertCircle size={12} />}
              {health ? 'Online' : 'Unavailable'}
            </span>
          </div>
        </div>
      </div>

      {/* Footer disclaimer */}
      <footer
        style={{
          marginTop: '3rem',
          marginBottom: 'auto',
          fontSize: '0.75rem',
          color: 'var(--text-muted)',
          textAlign: 'center',
          zIndex: 1,
        }}
      >
        Answers are generated only from documents you choose to index.
      </footer>
    </div>
  );
}
