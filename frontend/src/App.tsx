import { useEffect, useState } from 'react';
import {
  Plus,
  MessageSquare,
  Trash2,
  Database,
  FolderSync,
  LogOut,
  PanelLeftClose,
  PanelLeftOpen,
  X,
  CheckCircle2,
} from 'lucide-react';
import { HealthStatus, TelemetryStatus, OneDriveItem, UserProfile } from './types';
import {
  fetchHealth,
  fetchTelemetryStatus,
  fetchCurrentUser,
  logoutUser,
  exchangeAuthCode,
  exchangeGoogleAuthCode,
  startIngestion,
} from './services/api';
import { ChatBox } from './components/Chat/ChatBox';
import { FolderTree } from './components/OneDrive/FolderTree';
import { IngestionModal } from './components/OneDrive/IngestionModal';
import { KnowledgeBaseTable } from './components/Dashboard/KnowledgeBaseTable';
import { LoginPage } from './components/Auth/LoginPage';

interface ChatSessionItem {
  id: string;
  title: string;
  timestamp: string;
}

export function App() {
  const [currentUser, setCurrentUser] = useState<UserProfile | null>(null);
  const [authLoading, setAuthLoading] = useState<boolean>(true);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [telemetry, setTelemetry] = useState<TelemetryStatus | null>(null);

  // Conversational Chatbot Session Management
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessions, setSessions] = useState<ChatSessionItem[]>([
    {
      id: 'session_demo_1',
      title: 'Executive Briefing Q3 Summary',
      timestamp: 'Just now',
    },
    {
      id: 'session_demo_2',
      title: 'Cloud Infrastructure Budget Analysis',
      timestamp: 'Earlier today',
    },
  ]);

  // Sidebar and Slide-over Drawer
  const [isSidebarOpen, setIsSidebarOpen] = useState<boolean>(true);
  const [isKnowledgeDrawerOpen, setIsKnowledgeDrawerOpen] = useState<boolean>(false);
  const [knowledgeTab, setKnowledgeTab] = useState<'browse' | 'indexed'>('browse');
  const [activeJobId, setActiveJobId] = useState<string | null>(null);

  useEffect(() => {
    async function initApp() {
      try {
        setAuthLoading(true);

        // 1. Check for OAuth redirect code in URL
        const params = new URLSearchParams(window.location.search);
        const code = params.get('code');
        const state = params.get('state') || undefined;

        if (code) {
          try {
            const isGoogle =
              window.location.pathname.includes('google') ||
              code.startsWith('mock_google') ||
              (params.get('scope') && params.get('scope')!.includes('google'));

            const authRes = isGoogle
              ? await exchangeGoogleAuthCode(code, state)
              : await exchangeAuthCode(code, state);

            setCurrentUser(authRes.user);
            window.history.replaceState({}, document.title, window.location.pathname);
          } catch (e) {
            console.error('Failed to exchange OAuth code', e);
          }
        } else {
          // 2. Fetch authenticated profile
          const user = await fetchCurrentUser();
          setCurrentUser(user);
        }

        // 3. Load backend health
        const [h, t] = await Promise.all([fetchHealth(), fetchTelemetryStatus()]);
        setHealth(h);
        setTelemetry(t);
      } catch (err: any) {
        console.warn('Backend connection warning:', err.message);
      } finally {
        setAuthLoading(false);
      }
    }
    initApp();
  }, []);

  function handleNewChat() {
    setSessionId(null);
  }

  function handleSessionChange(newSessionId: string, firstQuery?: string) {
    setSessionId(newSessionId);
    if (firstQuery) {
      setSessions((prev) => {
        const title = firstQuery.length > 32 ? firstQuery.substring(0, 32) + '...' : firstQuery;
        const exists = prev.some((s) => s.id === newSessionId);
        if (exists) return prev;
        return [{ id: newSessionId, title, timestamp: 'Just now' }, ...prev];
      });
    }
  }

  function handleDeleteSession(e: React.MouseEvent, idToDelete: string) {
    e.stopPropagation();
    setSessions((prev) => prev.filter((s) => s.id !== idToDelete));
    if (sessionId === idToDelete) {
      setSessionId(null);
    }
  }

  async function handleIndexSelection(selectedIds: string[], items: OneDriveItem[]) {
    try {
      const data = await startIngestion(selectedIds, items, '/Company');
      setActiveJobId(data.job_id);
    } catch (e) {
      console.error('Failed to trigger ingestion', e);
    }
  }

  // Render Login Page if user is not authenticated
  if (!currentUser && !authLoading) {
    return (
      <LoginPage
        onLoginSuccess={(user) => setCurrentUser(user)}
        health={health}
        telemetry={telemetry}
      />
    );
  }

  return (
    <div
      style={{
        display: 'flex',
        height: '100vh',
        width: '100vw',
        overflow: 'hidden',
        background: 'var(--app-bg)',
        color: 'var(--app-text)',
      }}
    >
      {/* =========================================================================
          1. Collapsible Left Sidebar (Enterprise Cloud Theme)
         ========================================================================= */}
      {isSidebarOpen && (
        <aside
          style={{
            width: '265px',
            height: '100%',
            background: 'var(--app-sidebar)',
            borderRight: '1px solid var(--app-border)',
            display: 'flex',
            flexDirection: 'column',
            flexShrink: 0,
            zIndex: 30,
            transition: 'width 0.2s ease',
          }}
        >
          {/* Sidebar Top: Previous Logo & Name */}
          <div
            style={{
              padding: '1.1rem 1rem 0.85rem 1rem',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              borderBottom: '1px solid var(--app-border)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem' }}>
              {/* Previous Logo: Gradient rounded square with FolderSync */}
              <div
                style={{
                  width: '36px',
                  height: '36px',
                  borderRadius: '10px',
                  background: 'var(--brand-gradient)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  boxShadow: '0 4px 14px var(--brand-glow)',
                  flexShrink: 0,
                }}
              >
                <FolderSync size={20} color="#ffffff" />
              </div>

              {/* Previous Name: Enterprise Cloud RAG */}
              <div style={{ display: 'flex', flexDirection: 'column' }}>
                <h1
                  style={{
                    fontSize: '0.98rem',
                    fontWeight: 700,
                    letterSpacing: '-0.02em',
                    color: 'var(--app-text)',
                    lineHeight: 1.2,
                  }}
                >
                  Enterprise Cloud RAG
                </h1>
                <span style={{ fontSize: '0.68rem', color: 'var(--app-text-secondary)', lineHeight: 1.2 }}>
                  OneDrive & Google Drive
                </span>
              </div>
            </div>

            <button
              onClick={() => setIsSidebarOpen(false)}
              style={{
                background: 'transparent',
                border: 'none',
                color: 'var(--app-text-muted)',
                cursor: 'pointer',
                padding: '4px',
                borderRadius: '6px',
                display: 'flex',
                alignItems: 'center',
              }}
              title="Close sidebar"
              onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--app-text)')}
              onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--app-text-muted)')}
            >
              <PanelLeftClose size={18} />
            </button>
          </div>

          {/* New Chat Button */}
          <div style={{ padding: '0.75rem 1rem 0.5rem 1rem' }}>
            <button
              onClick={handleNewChat}
              style={{
                width: '100%',
                display: 'flex',
                alignItems: 'center',
                gap: '0.6rem',
                padding: '0.65rem 0.9rem',
                background: 'var(--app-surface)',
                border: '1px solid var(--app-border)',
                borderRadius: 'var(--radius-md)',
                color: 'var(--app-text)',
                fontSize: '0.875rem',
                fontWeight: 600,
                cursor: 'pointer',
                transition: 'all 0.15s ease',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.borderColor = 'var(--brand-primary)';
                e.currentTarget.style.background = 'var(--app-surface-hover)';
                e.currentTarget.style.boxShadow = '0 2px 8px var(--brand-glow)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.borderColor = 'var(--app-border)';
                e.currentTarget.style.background = 'var(--app-surface)';
                e.currentTarget.style.boxShadow = 'none';
              }}
            >
              <Plus size={16} color="var(--brand-cyan)" />
              <span>Start new chat</span>
            </button>
          </div>

          {/* Recent Conversations List */}
          <div
            style={{
              flex: 1,
              overflowY: 'auto',
              padding: '0.25rem 0.5rem',
              display: 'flex',
              flexDirection: 'column',
              gap: '0.2rem',
            }}
          >
            <div
              style={{
                fontSize: '0.7rem',
                fontWeight: 700,
                color: 'var(--app-text-muted)',
                padding: '0.5rem 0.6rem 0.25rem 0.6rem',
                textTransform: 'uppercase',
                letterSpacing: '0.05em',
              }}
            >
              Recent Chats
            </div>

            {sessions.map((s) => {
              const isActive = sessionId === s.id;
              return (
                <div
                  key={s.id}
                  onClick={() => setSessionId(s.id)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '0.55rem 0.75rem',
                    borderRadius: 'var(--radius-md)',
                    background: isActive ? 'var(--brand-bg)' : 'transparent',
                    border: isActive ? '1px solid rgba(0, 120, 212, 0.4)' : '1px solid transparent',
                    cursor: 'pointer',
                    transition: 'all 0.15s ease',
                    color: isActive ? '#60a5fa' : 'var(--app-text-secondary)',
                  }}
                  onMouseEnter={(e) => {
                    if (!isActive) e.currentTarget.style.background = 'var(--app-sidebar-hover)';
                  }}
                  onMouseLeave={(e) => {
                    if (!isActive) e.currentTarget.style.background = 'transparent';
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', minWidth: 0 }}>
                    <MessageSquare size={14} color={isActive ? '#38bdf8' : 'var(--app-text-muted)'} />
                    <span
                      style={{
                        fontSize: '0.83rem',
                        fontWeight: isActive ? 600 : 400,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {s.title}
                    </span>
                  </div>

                  <button
                    onClick={(e) => handleDeleteSession(e, s.id)}
                    style={{
                      background: 'transparent',
                      border: 'none',
                      color: 'var(--app-text-muted)',
                      cursor: 'pointer',
                      padding: '2px',
                      display: 'flex',
                      alignItems: 'center',
                      opacity: 0.6,
                    }}
                    title="Delete chat"
                    onMouseEnter={(e) => {
                      e.currentTarget.style.color = '#fb7185';
                      e.currentTarget.style.opacity = '1';
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.color = 'var(--app-text-muted)';
                      e.currentTarget.style.opacity = '0.6';
                    }}
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              );
            })}
          </div>

          {/* Cloud Storage Knowledge Shortcut Card */}
          <div style={{ padding: '0.75rem 1rem', borderTop: '1px solid var(--app-border)' }}>
            <div
              style={{
                padding: '0.75rem',
                background: 'var(--app-surface)',
                border: '1px solid var(--app-border)',
                borderRadius: 'var(--radius-md)',
              }}
            >
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  marginBottom: '0.4rem',
                }}
              >
                <span style={{ fontSize: '0.78rem', fontWeight: 600, color: 'var(--app-text)' }}>
                  Connected Drives
                </span>
                <span
                  style={{
                    fontSize: '0.65rem',
                    color: '#34d399',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '2px',
                  }}
                >
                  <CheckCircle2 size={11} /> Synced
                </span>
              </div>

              {/* Provider Badges */}
              <div style={{ display: 'flex', gap: '0.4rem', marginBottom: '0.6rem' }}>
                <span
                  style={{
                    fontSize: '0.68rem',
                    padding: '0.15rem 0.45rem',
                    borderRadius: '4px',
                    background: 'var(--onedrive-blue-bg)',
                    color: '#93c5fd',
                    fontWeight: 600,
                  }}
                >
                  OneDrive
                </span>
                <span
                  style={{
                    fontSize: '0.68rem',
                    padding: '0.15rem 0.45rem',
                    borderRadius: '4px',
                    background: 'var(--gdrive-green-bg)',
                    color: '#6ee7b7',
                    fontWeight: 600,
                  }}
                >
                  Google Drive
                </span>
              </div>

              <button
                onClick={() => {
                  setKnowledgeTab('browse');
                  setIsKnowledgeDrawerOpen(true);
                }}
                style={{
                  width: '100%',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: '0.4rem',
                  padding: '0.45rem 0.5rem',
                  background: 'rgba(255, 255, 255, 0.05)',
                  border: '1px solid var(--app-border)',
                  borderRadius: 'var(--radius-sm)',
                  color: 'var(--app-text)',
                  fontSize: '0.75rem',
                  fontWeight: 600,
                  cursor: 'pointer',
                  transition: 'all 0.15s ease',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.borderColor = 'var(--brand-primary)';
                  e.currentTarget.style.background = 'var(--app-surface-hover)';
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.borderColor = 'var(--app-border)';
                  e.currentTarget.style.background = 'rgba(255, 255, 255, 0.05)';
                }}
              >
                <FolderSync size={13} color="var(--brand-cyan)" />
                <span>Manage Knowledge Base</span>
              </button>
            </div>
          </div>

          {/* User Profile Bar & Sign Out */}
          {currentUser && (
            <div
              style={{
                padding: '0.75rem 1rem',
                borderTop: '1px solid var(--app-border)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', minWidth: 0 }}>
                <div
                  style={{
                    width: '32px',
                    height: '32px',
                    borderRadius: '50%',
                    background: 'var(--brand-gradient)',
                    color: '#ffffff',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: '0.85rem',
                    fontWeight: 700,
                    flexShrink: 0,
                    boxShadow: '0 2px 8px var(--brand-glow)',
                  }}
                >
                  {currentUser.full_name ? currentUser.full_name[0].toUpperCase() : 'U'}
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
                  <span
                    style={{
                      fontSize: '0.82rem',
                      fontWeight: 600,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {currentUser.full_name || 'Enterprise User'}
                  </span>
                  <span
                    style={{
                      fontSize: '0.68rem',
                      color: 'var(--app-text-muted)',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {currentUser.email}
                  </span>
                </div>
              </div>

              <button
                onClick={async () => {
                  await logoutUser();
                  setCurrentUser(null);
                }}
                title="Sign out"
                style={{
                  background: 'transparent',
                  border: 'none',
                  color: 'var(--app-text-muted)',
                  cursor: 'pointer',
                  padding: '6px',
                  borderRadius: '4px',
                  display: 'flex',
                  alignItems: 'center',
                }}
                onMouseEnter={(e) => (e.currentTarget.style.color = '#fb7185')}
                onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--app-text-muted)')}
              >
                <LogOut size={16} />
              </button>
            </div>
          )}
        </aside>
      )}

      {/* =========================================================================
          2. Main Chat Canvas
         ========================================================================= */}
      <main
        style={{
          flex: 1,
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          position: 'relative',
          overflow: 'hidden',
          background: 'var(--app-bg)',
        }}
      >
        {/* Toggle Sidebar Button (shown when sidebar is closed) */}
        {!isSidebarOpen && (
          <button
            onClick={() => setIsSidebarOpen(true)}
            style={{
              position: 'absolute',
              top: '0.85rem',
              left: '1rem',
              zIndex: 40,
              background: 'var(--app-surface)',
              border: '1px solid var(--app-border)',
              borderRadius: 'var(--radius-md)',
              color: 'var(--app-text-secondary)',
              cursor: 'pointer',
              padding: '6px 8px',
              display: 'flex',
              alignItems: 'center',
              gap: '0.45rem',
              fontSize: '0.8rem',
              boxShadow: '0 2px 8px rgba(0,0,0,0.3)',
            }}
            title="Open sidebar"
            onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--app-text)')}
            onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--app-text-secondary)')}
          >
            <PanelLeftOpen size={16} />
            <span style={{ color: 'var(--brand-cyan)', fontWeight: 700 }}>Enterprise Cloud RAG</span>
          </button>
        )}

        {/* Clean Conversational Chat Experience */}
        <ChatBox
          sessionId={sessionId}
          onSessionChange={handleSessionChange}
          onOpenKnowledgeDrawer={() => setIsKnowledgeDrawerOpen(true)}
          userName={currentUser?.full_name?.split(' ')[0] || 'there'}
        />
      </main>

      {/* =========================================================================
          3. Slide-Over Knowledge & Cloud Drive Drawer
         ========================================================================= */}
      {isKnowledgeDrawerOpen && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 100,
            display: 'flex',
            justifyContent: 'flex-end',
          }}
        >
          {/* Backdrop */}
          <div
            onClick={() => setIsKnowledgeDrawerOpen(false)}
            style={{
              position: 'absolute',
              inset: 0,
              background: 'rgba(0, 0, 0, 0.65)',
              backdropFilter: 'blur(4px)',
            }}
          />

          {/* Drawer Container */}
          <div
            style={{
              position: 'relative',
              width: 'min(760px, 94vw)',
              height: '100%',
              background: 'var(--app-surface)',
              borderLeft: '1px solid var(--app-border)',
              boxShadow: '-8px 0 32px rgba(0, 0, 0, 0.6)',
              display: 'flex',
              flexDirection: 'column',
              zIndex: 101,
            }}
            className="animate-fade-in"
          >
            {/* Drawer Header */}
            <div
              style={{
                padding: '1.25rem 1.5rem',
                borderBottom: '1px solid var(--app-border)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem' }}>
                <div
                  style={{
                    width: '32px',
                    height: '32px',
                    borderRadius: '8px',
                    background: 'var(--brand-gradient)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: '#ffffff',
                    boxShadow: '0 2px 8px var(--brand-glow)',
                  }}
                >
                  <Database size={17} />
                </div>
                <div>
                  <h3 style={{ fontSize: '1.05rem', fontWeight: 700 }}>
                    Cloud Drive Knowledge Base
                  </h3>
                  <span style={{ fontSize: '0.75rem', color: 'var(--app-text-muted)' }}>
                    OneDrive & Google Drive documents indexed in pgvector
                  </span>
                </div>
              </div>

              <button
                onClick={() => setIsKnowledgeDrawerOpen(false)}
                style={{
                  background: 'transparent',
                  border: 'none',
                  color: 'var(--app-text-muted)',
                  cursor: 'pointer',
                  padding: '6px',
                  borderRadius: '6px',
                  display: 'flex',
                  alignItems: 'center',
                }}
                title="Close drawer"
                onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--app-text)')}
                onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--app-text-muted)')}
              >
                <X size={20} />
              </button>
            </div>

            {/* Subtabs inside Drawer */}
            <div
              style={{
                display: 'flex',
                gap: '0.5rem',
                padding: '0.75rem 1.5rem',
                borderBottom: '1px solid var(--app-border)',
                background: 'rgba(0, 0, 0, 0.2)',
              }}
            >
              <button
                onClick={() => setKnowledgeTab('browse')}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.4rem',
                  padding: '0.45rem 0.85rem',
                  borderRadius: 'var(--radius-md)',
                  background: knowledgeTab === 'browse' ? 'var(--brand-bg)' : 'transparent',
                  color: knowledgeTab === 'browse' ? '#60a5fa' : 'var(--app-text-secondary)',
                  border: knowledgeTab === 'browse' ? '1px solid rgba(0, 120, 212, 0.4)' : '1px solid transparent',
                  fontWeight: 600,
                  fontSize: '0.8125rem',
                  cursor: 'pointer',
                  transition: 'all 0.15s ease',
                }}
              >
                <FolderSync size={15} />
                <span>Browse & Index Cloud Drives</span>
              </button>

              <button
                onClick={() => setKnowledgeTab('indexed')}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.4rem',
                  padding: '0.45rem 0.85rem',
                  borderRadius: 'var(--radius-md)',
                  background: knowledgeTab === 'indexed' ? 'var(--brand-bg)' : 'transparent',
                  color: knowledgeTab === 'indexed' ? '#60a5fa' : 'var(--app-text-secondary)',
                  border: knowledgeTab === 'indexed' ? '1px solid rgba(0, 120, 212, 0.4)' : '1px solid transparent',
                  fontWeight: 600,
                  fontSize: '0.8125rem',
                  cursor: 'pointer',
                  transition: 'all 0.15s ease',
                }}
              >
                <Database size={15} />
                <span>Indexed Documents & Chunks</span>
              </button>
            </div>

            {/* Drawer Body Scroll View */}
            <div
              style={{
                flex: 1,
                overflowY: 'auto',
                padding: '1.5rem',
              }}
            >
              {knowledgeTab === 'browse' ? (
                <FolderTree
                  onIndexSelection={handleIndexSelection}
                  isIndexing={Boolean(activeJobId)}
                />
              ) : (
                <KnowledgeBaseTable />
              )}
            </div>
          </div>
        </div>
      )}

      {/* Ingestion Progress Modal */}
      {activeJobId && (
        <IngestionModal
          jobId={activeJobId}
          onClose={() => setActiveJobId(null)}
          onComplete={() => {
            // Refreshes data
          }}
        />
      )}
    </div>
  );
}

export default App;
