import { useCallback, useEffect, useRef, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import {
  Plus,
  MessageSquare,
  Trash2,
  Database,
  FolderSync,
  LogOut,
  PanelLeftClose,
  X,
  CheckCircle2,
  Sun,
  Moon,
  AlertCircle,
} from 'lucide-react';
import { DriveType, HealthStatus, UserProfile } from './types';
import {
  AUTH_EXPIRED_EVENT,
  ApiError,
  IngestSelection,
  OAuthProvider,
  TOKEN_STORAGE_KEY,
  completeOAuth,
  discardOAuthAttempt,
  fetchHealth,
  fetchCurrentUser,
  hasSessionToken,
  logoutUser,
  startIngestion,
  fetchChatSessions,
  deleteChatSession,
} from './services/api';
import { MOBILE_QUERY, useDialogFocus, useMediaQuery } from './hooks';
import { ChatBox } from './components/Chat/ChatBox';
import { FolderTree } from './components/OneDrive/FolderTree';
import { IngestionModal } from './components/OneDrive/IngestionModal';
import { KnowledgeBaseTable } from './components/Dashboard/KnowledgeBaseTable';
import { LoginPage } from './components/Auth/LoginPage';

type Theme = 'dark' | 'light';
type KnowledgeTab = 'browse' | 'indexed';

interface ChatSessionItem {
  id: string;
  title: string;
  /** Raw ISO timestamp; the relative label is computed at render time so it stays correct. */
  updatedAt: string | null;
}

/** Outcome of an OAuth round trip that connected a drive while the user was already signed in. */
interface ConnectResult {
  drive: DriveType;
  error: string | null;
}

const SESSION_EXPIRED_MESSAGE = 'Your session has expired. Please sign in again.';

const driveName = (drive: DriveType) => (drive === 'google_drive' ? 'Google Drive' : 'OneDrive');

const shortTitle = (query: string) => (query.length > 60 ? `${query.substring(0, 60)}...` : query);

function formatRelativeTime(dateStr: string | null | undefined, now: number): string {
  if (!dateStr) return 'Recently';
  const time = new Date(dateStr).getTime();
  if (isNaN(time)) return 'Recently';
  const diffSec = Math.floor((now - time) / 1000);
  if (diffSec < 60) return 'Just now';
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  if (diffSec < 172800) return 'Yesterday';
  return new Date(time).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export function App() {
  const [currentUser, setCurrentUser] = useState<UserProfile | null>(null);
  const [authLoading, setAuthLoading] = useState<boolean>(true);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);
  const [connectResult, setConnectResult] = useState<ConnectResult | null>(null);
  const initStartedRef = useRef(false);
  const currentUserRef = useRef<UserProfile | null>(null);
  useEffect(() => {
    currentUserRef.current = currentUser;
  }, [currentUser]);

  // Theme Mode (Dark / Light) with localStorage persistence
  const [theme, setTheme] = useState<Theme>(() => {
    const saved = localStorage.getItem('onedrive_rag_theme');
    return saved === 'light' || saved === 'dark' ? saved : 'dark';
  });

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('onedrive_rag_theme', theme);
  }, [theme]);

  const toggleTheme = useCallback(() => {
    setTheme((prev) => (prev === 'dark' ? 'light' : 'dark'));
  }, []);

  /** Leaves the workspace; everything tied to the signed-in user is dropped with it (see the key below). */
  const signOutLocally = useCallback((message: string | null) => {
    currentUserRef.current = null;
    setCurrentUser(null);
    setConnectResult(null);
    setAuthError(message);
  }, []);

  useEffect(() => {
    // Once only, also under StrictMode: an OAuth code can be redeemed a single time.
    if (initStartedRef.current) return;
    initStartedRef.current = true;

    async function initApp() {
      fetchHealth().then(setHealth).catch(() => setHealth(null));
      let oauthError: string | null = null;
      let connectedDrive: DriveType | null = null;
      let signedIn: UserProfile | null = null;
      try {
        const params = new URLSearchParams(window.location.search);
        const code = params.get('code');
        const providerError = params.get('error_description') || params.get('error');
        if (code || providerError) {
          // OAuth redirect back from Google (/auth/google/callback). Microsoft sign-in (/auth/callback) is turned off:
          // const provider: OAuthProvider = window.location.pathname.includes('google') ? 'google' : 'microsoft';
          const provider: OAuthProvider = 'google';
          // Already signed in: this round trip connected a drive from the drive browser.
          // if (hasSessionToken()) connectedDrive = provider === 'google' ? 'google_drive' : 'onedrive';
          if (hasSessionToken()) connectedDrive = 'google_drive';
          window.history.replaceState({}, document.title, '/');
          if (providerError) {
            oauthError = providerError;
            discardOAuthAttempt();
          } else {
            try {
              signedIn = await completeOAuth(provider, code!, params.get('state'));
            } catch (e) {
              oauthError = (e as Error).message;
            }
          }
        }
        const user = (await fetchCurrentUser().catch(() => null)) ?? signedIn;
        setCurrentUser(user);
        if (user && connectedDrive) {
          setConnectResult({ drive: connectedDrive, error: oauthError });
        } else {
          setAuthError(oauthError);
        }
      } finally {
        setAuthLoading(false);
      }
    }
    initApp();
  }, []);

  useEffect(() => {
    // Ignored once signed out: a stale request finishing after "Sign out" is not an expired session.
    const onExpired = () => {
      if (currentUserRef.current) signOutLocally(SESSION_EXPIRED_MESSAGE);
    };
    // Another tab signed out or in (key null means its storage was cleared).
    const onStorage = (e: StorageEvent) => {
      if (e.key !== null && e.key !== TOKEN_STORAGE_KEY) return;
      if (!hasSessionToken()) {
        signOutLocally(null);
        return;
      }
      fetchCurrentUser()
        .then((user) => {
          if (!user) {
            signOutLocally(null);
            return;
          }
          setCurrentUser(user);
          setAuthError(null);
        })
        .catch(() => undefined);
    };
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    window.addEventListener('storage', onStorage);
    return () => {
      window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
      window.removeEventListener('storage', onStorage);
    };
  }, [signOutLocally]);

  const handleSignOut = useCallback(async () => {
    await logoutUser();
    signOutLocally(null);
  }, [signOutLocally]);

  const clearConnectResult = useCallback(() => setConnectResult(null), []);

  if (authLoading) return <Splash />;

  // Render Login Page if user is not authenticated
  if (!currentUser) {
    return (
      <LoginPage
        onLoginSuccess={(user) => {
          setAuthError(null);
          setCurrentUser(user);
        }}
        health={health}
        initialError={authError}
        theme={theme}
        onToggleTheme={toggleTheme}
      />
    );
  }

  // Keyed by user: signing out or switching accounts starts from a clean workspace (chat, drawer, jobs).
  return (
    <Workspace
      key={currentUser.id}
      user={currentUser}
      theme={theme}
      onToggleTheme={toggleTheme}
      onSignOut={handleSignOut}
      connectResult={connectResult}
      onConnectResultSeen={clearConnectResult}
    />
  );
}

/** Neutral placeholder while the session is being checked (nothing interactive yet). */
function Splash() {
  return (
    <div
      role="status"
      aria-live="polite"
      style={{
        flex: 1,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: '0.85rem',
        background: 'var(--app-bg)',
        color: 'var(--app-text-muted)',
        fontSize: '0.85rem',
      }}
    >
      <div
        style={{
          width: '44px',
          height: '44px',
          borderRadius: '12px',
          background: 'var(--brand-gradient)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          boxShadow: '0 8px 24px var(--brand-glow)',
        }}
      >
        <FolderSync size={24} color="#ffffff" aria-hidden="true" />
      </div>
      <span>Loading...</span>
    </div>
  );
}

function NoticeBanner({ text, onDismiss, style }: { text: string; onDismiss: () => void; style?: CSSProperties }) {
  return (
    <div
      role="alert"
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: '0.6rem',
        padding: '0.7rem 1rem',
        borderRadius: 'var(--radius-md)',
        background: 'var(--danger-bg)',
        border: '1px solid var(--danger-border)',
        color: 'var(--danger-text)',
        fontSize: '0.825rem',
        ...style,
      }}
    >
      <AlertCircle size={16} style={{ flexShrink: 0, marginTop: '2px' }} aria-hidden="true" />
      <span style={{ flex: 1, overflowWrap: 'anywhere' }}>{text}</span>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss message"
        style={{ background: 'transparent', border: 'none', color: 'inherit', cursor: 'pointer', display: 'flex', padding: '2px' }}
      >
        <X size={14} />
      </button>
    </div>
  );
}

interface WorkspaceProps {
  user: UserProfile;
  theme: Theme;
  onToggleTheme: () => void;
  onSignOut: () => void;
  connectResult: ConnectResult | null;
  onConnectResultSeen: () => void;
}

function Workspace({ user, theme, onToggleTheme, onSignOut, connectResult, onConnectResultSeen }: WorkspaceProps) {
  const isMobile = useMediaQuery(MOBILE_QUERY);

  // Conversational Chatbot Session Management
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessions, setSessions] = useState<ChatSessionItem[]>([]);
  const sessionsRequestRef = useRef(0);
  const deletedIdsRef = useRef(new Set<string>());

  // Sidebar (an overlay on small screens) and Slide-over Drawer. Returning from connecting a drive
  // reopens the drawer on that drive.
  const [isSidebarOpen, setIsSidebarOpen] = useState<boolean>(() => !window.matchMedia(MOBILE_QUERY).matches);
  const [isKnowledgeDrawerOpen, setIsKnowledgeDrawerOpen] = useState<boolean>(() => connectResult !== null);
  const [knowledgeTab, setKnowledgeTab] = useState<KnowledgeTab>('browse');
  const [browseDrive, setBrowseDrive] = useState<DriveType>(
    () => connectResult?.drive ?? (!user.onedrive_connected && user.gdrive_connected ? 'google_drive' : 'onedrive')
  );
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [kbRefreshToken, setKbRefreshToken] = useState<number>(0);
  const [notice, setNotice] = useState<string | null>(() =>
    connectResult?.error ? `Connecting ${driveName(connectResult.drive)} did not finish: ${connectResult.error}` : null
  );

  useEffect(() => {
    if (connectResult) onConnectResultSeen();
  }, [connectResult, onConnectResultSeen]);

  useEffect(() => {
    setIsSidebarOpen(!isMobile);
  }, [isMobile]);

  const loadUserSessions = useCallback(async () => {
    const request = ++sessionsRequestRef.current;
    try {
      const data = await fetchChatSessions();
      if (request !== sessionsRequestRef.current) return; // a newer reload is on its way
      setSessions(
        data
          .filter((s) => !deletedIdsRef.current.has(s.id))
          .map((s) => ({ id: s.id, title: s.title || 'Untitled Chat', updatedAt: s.updated_at || s.created_at || null }))
      );
    } catch (e) {
      console.error('Failed to load past chat sessions', e);
    }
  }, []);

  useEffect(() => {
    loadUserSessions();
  }, [loadUserSessions]);

  function closeSidebarOnMobile() {
    if (isMobile) setIsSidebarOpen(false);
  }

  function selectSession(id: string | null) {
    setSessionId(id);
    closeSidebarOnMobile();
  }

  const handleSessionChange = useCallback(
    (newSessionId: string, firstQuery?: string) => {
      setSessionId(newSessionId);
      if (firstQuery) {
        setSessions((prev) =>
          prev.some((s) => s.id === newSessionId)
            ? prev
            : [{ id: newSessionId, title: shortTitle(firstQuery), updatedAt: new Date().toISOString() }, ...prev]
        );
      }
      // Re-sync with backend to get persisted title and updated timestamp
      loadUserSessions();
    },
    [loadUserSessions]
  );

  async function handleDeleteSession(target: ChatSessionItem) {
    if (!window.confirm(`Delete the chat "${target.title}"? This cannot be undone.`)) return;
    const index = sessions.findIndex((s) => s.id === target.id);
    const wasActive = sessionId === target.id;
    deletedIdsRef.current.add(target.id);
    setSessions((prev) => prev.filter((s) => s.id !== target.id));
    if (wasActive) setSessionId(null);
    try {
      await deleteChatSession(target.id);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) return; // already deleted elsewhere
      // Roll back the optimistic removal.
      deletedIdsRef.current.delete(target.id);
      setSessions((prev) => {
        if (prev.some((s) => s.id === target.id)) return prev;
        const next = [...prev];
        next.splice(Math.max(0, Math.min(index, next.length)), 0, target);
        return next;
      });
      if (wasActive) setSessionId((current) => current ?? target.id);
      setNotice(`Could not delete the chat: ${(err as Error).message}`);
    }
  }

  async function handleIndexSelection(items: IngestSelection[]) {
    const job = await startIngestion(items);
    setActiveJobId(job.job_id);
  }

  function openDrawer(tab?: KnowledgeTab) {
    if (tab) setKnowledgeTab(tab);
    setIsKnowledgeDrawerOpen(true);
    closeSidebarOnMobile();
  }

  const closeDrawer = useCallback(() => setIsKnowledgeDrawerOpen(false), []);
  const closeIngestionModal = useCallback(() => setActiveJobId(null), []);
  const handleIngestionComplete = useCallback(() => setKbRefreshToken((t) => t + 1), []);
  const showIndexedDocuments = useCallback(() => {
    setActiveJobId(null);
    setKnowledgeTab('indexed');
    setIsKnowledgeDrawerOpen(true);
    setKbRefreshToken((t) => t + 1);
  }, []);

  const drives = [
    { id: 'onedrive', label: 'OneDrive', connected: Boolean(user.onedrive_connected), background: 'var(--onedrive-blue-bg)', color: 'var(--onedrive-text)' },
    { id: 'google_drive', label: 'Google Drive', connected: Boolean(user.gdrive_connected), background: 'var(--gdrive-green-bg)', color: 'var(--gdrive-text)' },
  ];
  const connectedCount = drives.filter((d) => d.connected).length;

  return (
    <div
      style={{
        display: 'flex',
        height: '100%',
        width: '100%',
        overflow: 'hidden',
        background: 'var(--app-bg)',
        color: 'var(--app-text)',
      }}
    >
      {isSidebarOpen && isMobile && (
        <div
          aria-hidden="true"
          onClick={() => setIsSidebarOpen(false)}
          style={{ position: 'fixed', inset: 0, zIndex: 40, background: 'rgba(0, 0, 0, 0.55)' }}
        />
      )}

      {/* =========================================================================
          1. Collapsible Left Sidebar (Enterprise Cloud Theme)
         ========================================================================= */}
      {isSidebarOpen && (
        <aside
          aria-label="Chats and drives"
          onKeyDown={(e) => {
            if (isMobile && e.key === 'Escape') setIsSidebarOpen(false);
          }}
          style={{
            width: '265px',
            maxWidth: '85vw',
            height: '100%',
            background: 'var(--app-sidebar)',
            borderRight: '1px solid var(--app-border)',
            display: 'flex',
            flexDirection: 'column',
            flexShrink: 0,
            zIndex: isMobile ? 50 : 30,
            transition: 'width 0.2s ease',
            ...(isMobile
              ? { position: 'fixed', top: 0, left: 0, bottom: 0, boxShadow: '8px 0 32px rgba(0, 0, 0, 0.45)' }
              : {}),
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
              type="button"
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
              aria-label="Close sidebar"
              onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--app-text)')}
              onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--app-text-muted)')}
            >
              <PanelLeftClose size={18} />
            </button>
          </div>

          {/* New Chat Button */}
          <div style={{ padding: '0.75rem 1rem 0.5rem 1rem' }}>
            <button
              type="button"
              onClick={() => selectSession(null)}
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
          <nav
            aria-label="Recent chats"
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

            <RecentChats sessions={sessions} activeId={sessionId} onSelect={selectSession} onDelete={handleDeleteSession} />
          </nav>

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
                    color: connectedCount > 0 ? 'var(--success-text)' : 'var(--app-text-muted)',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '2px',
                  }}
                >
                  {connectedCount > 0 && <CheckCircle2 size={11} aria-hidden="true" />}
                  {connectedCount === 0 ? 'None connected' : `${connectedCount} of ${drives.length} connected`}
                </span>
              </div>

              {/* Provider Badges */}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', marginBottom: '0.6rem' }}>
                {drives.map((d) => (
                  <span
                    key={d.id}
                    title={d.connected ? `${d.label} is connected` : `${d.label} is not connected. Open Manage Knowledge Base to connect it.`}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '0.25rem',
                      fontSize: '0.68rem',
                      padding: '0.15rem 0.45rem',
                      borderRadius: '4px',
                      fontWeight: 600,
                      ...(d.connected
                        ? { background: d.background, color: d.color, border: '1px solid transparent' }
                        : { background: 'transparent', color: 'var(--app-text-muted)', border: '1px dashed var(--app-border)' }),
                    }}
                  >
                    {d.connected && <CheckCircle2 size={10} aria-hidden="true" />}
                    {d.label}
                    <span className="sr-only">{d.connected ? ' (connected)' : ' (not connected)'}</span>
                  </span>
                ))}
              </div>

              <button
                type="button"
                onClick={() => openDrawer('browse')}
                style={{
                  width: '100%',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: '0.4rem',
                  padding: '0.45rem 0.5rem',
                  background: 'var(--app-sidebar-hover)',
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
                  e.currentTarget.style.background = 'var(--app-sidebar-hover)';
                }}
              >
                <FolderSync size={13} color="var(--brand-cyan)" />
                <span>Manage Knowledge Base</span>
              </button>
            </div>
          </div>

          {/* User Profile Bar & Sign Out */}
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
                aria-hidden="true"
              >
                {user.full_name ? user.full_name[0].toUpperCase() : 'U'}
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
                  {user.full_name || 'Enterprise User'}
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
                  {user.email}
                </span>
              </div>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
              <button
                type="button"
                onClick={onToggleTheme}
                title={`Switch to ${theme === 'dark' ? 'Light' : 'Dark'} mode`}
                aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
                style={{
                  background: 'transparent',
                  border: 'none',
                  color: 'var(--app-text-muted)',
                  cursor: 'pointer',
                  padding: '6px',
                  borderRadius: '4px',
                  display: 'flex',
                  alignItems: 'center',
                  transition: 'color 0.15s ease',
                }}
                onMouseEnter={(e) => (e.currentTarget.style.color = theme === 'dark' ? '#f59e0b' : '#0284c7')}
                onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--app-text-muted)')}
              >
                {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
              </button>

              <button
                type="button"
                onClick={onSignOut}
                title="Sign out"
                aria-label="Sign out"
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
                onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--danger-text)')}
                onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--app-text-muted)')}
              >
                <LogOut size={16} />
              </button>
            </div>
          </div>
        </aside>
      )}

      {/* =========================================================================
          2. Main Chat Canvas
         ========================================================================= */}
      <main
        style={{
          flex: 1,
          minWidth: 0,
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          position: 'relative',
          overflow: 'hidden',
          background: 'var(--app-bg)',
        }}
      >
        {notice && !isKnowledgeDrawerOpen && (
          <NoticeBanner text={notice} onDismiss={() => setNotice(null)} style={{ margin: '0.75rem 1rem 0 1rem' }} />
        )}

        {/* Clean Conversational Chat Experience */}
        <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
          <ChatBox
            sessionId={sessionId}
            onSessionChange={handleSessionChange}
            onAnswerComplete={loadUserSessions}
            onOpenKnowledgeDrawer={() => openDrawer()}
            userName={user.full_name?.split(' ')[0] || 'there'}
            isSidebarOpen={isSidebarOpen}
            onOpenSidebar={() => setIsSidebarOpen(true)}
            theme={theme}
            onToggleTheme={onToggleTheme}
          />
        </div>
      </main>

      {/* =========================================================================
          3. Slide-Over Knowledge & Cloud Drive Drawer
         ========================================================================= */}
      {isKnowledgeDrawerOpen && (
        <KnowledgeDrawer tab={knowledgeTab} onTabChange={setKnowledgeTab} onClose={closeDrawer} compact={isMobile}>
          {notice && <NoticeBanner text={notice} onDismiss={() => setNotice(null)} style={{ marginBottom: '1rem' }} />}
          {knowledgeTab === 'browse' ? (
            <FolderTree
              onIndexSelection={handleIndexSelection}
              isIndexing={Boolean(activeJobId)}
              initialDrive={browseDrive}
              onDriveChange={setBrowseDrive}
            />
          ) : (
            <KnowledgeBaseTable refreshToken={kbRefreshToken} />
          )}
        </KnowledgeDrawer>
      )}

      {/* Ingestion Progress Modal */}
      {activeJobId && (
        <IngestionModal
          jobId={activeJobId}
          onClose={closeIngestionModal}
          onComplete={handleIngestionComplete}
          onViewDocuments={showIndexedDocuments}
        />
      )}
    </div>
  );
}

function RecentChats({
  sessions,
  activeId,
  onSelect,
  onDelete,
}: {
  sessions: ChatSessionItem[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onDelete: (session: ChatSessionItem) => void;
}) {
  // Re-render every minute so "Just now" turns into "5m ago" without other activity.
  const [, setTick] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => setTick((t) => t + 1), 60_000);
    return () => window.clearInterval(timer);
  }, []);
  const now = Date.now();

  if (sessions.length === 0) {
    return (
      <div
        style={{
          padding: '0.6rem 0.75rem',
          fontSize: '0.78rem',
          color: 'var(--app-text-muted)',
          fontStyle: 'italic',
        }}
      >
        No past chats yet
      </div>
    );
  }

  return (
    <>
      {sessions.map((s) => {
        const isActive = activeId === s.id;
        return (
          <div
            key={s.id}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              borderRadius: 'var(--radius-md)',
              background: isActive ? 'var(--brand-bg)' : 'transparent',
              border: isActive ? '1px solid rgba(0, 120, 212, 0.4)' : '1px solid transparent',
              transition: 'all 0.15s ease',
              color: isActive ? 'var(--info-text)' : 'var(--app-text-secondary)',
            }}
            onMouseEnter={(e) => {
              if (!isActive) e.currentTarget.style.background = 'var(--app-sidebar-hover)';
            }}
            onMouseLeave={(e) => {
              if (!isActive) e.currentTarget.style.background = 'transparent';
            }}
          >
            <button
              type="button"
              onClick={() => onSelect(s.id)}
              aria-current={isActive ? 'true' : undefined}
              style={{
                flex: 1,
                minWidth: 0,
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
                padding: '0.55rem 0.25rem 0.55rem 0.75rem',
                background: 'transparent',
                border: 'none',
                borderRadius: 'var(--radius-md)',
                color: 'inherit',
                cursor: 'pointer',
                textAlign: 'left',
                fontFamily: 'inherit',
                lineHeight: 'inherit',
              }}
            >
              <MessageSquare size={14} color={isActive ? '#38bdf8' : 'var(--app-text-muted)'} style={{ flexShrink: 0 }} />
              <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0, overflow: 'hidden' }}>
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
                <span style={{ fontSize: '0.68rem', color: 'var(--app-text-muted)', marginTop: '1px' }}>
                  {formatRelativeTime(s.updatedAt, now)}
                </span>
              </span>
            </button>

            <button
              type="button"
              onClick={() => onDelete(s)}
              style={{
                background: 'transparent',
                border: 'none',
                color: 'var(--app-text-muted)',
                cursor: 'pointer',
                padding: '2px',
                marginRight: '0.75rem',
                display: 'flex',
                alignItems: 'center',
                opacity: 0.6,
                flexShrink: 0,
              }}
              title="Delete chat"
              aria-label={`Delete chat "${s.title}"`}
              onMouseEnter={(e) => {
                e.currentTarget.style.color = 'var(--danger-text)';
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
    </>
  );
}

function KnowledgeDrawer({
  tab,
  onTabChange,
  onClose,
  compact,
  children,
}: {
  tab: KnowledgeTab;
  onTabChange: (tab: KnowledgeTab) => void;
  onClose: () => void;
  compact: boolean;
  children: ReactNode;
}) {
  const { ref, onKeyDown } = useDialogFocus<HTMLDivElement>(onClose);

  const tabStyle = (active: boolean): CSSProperties => ({
    display: 'flex',
    alignItems: 'center',
    gap: '0.4rem',
    padding: '0.45rem 0.85rem',
    borderRadius: 'var(--radius-md)',
    background: active ? 'var(--brand-bg)' : 'transparent',
    color: active ? 'var(--info-text)' : 'var(--app-text-secondary)',
    border: active ? '1px solid rgba(0, 120, 212, 0.4)' : '1px solid transparent',
    fontWeight: 600,
    fontSize: '0.8125rem',
    cursor: 'pointer',
    transition: 'all 0.15s ease',
  });

  return (
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
        aria-hidden="true"
        onClick={onClose}
        style={{
          position: 'absolute',
          inset: 0,
          background: 'rgba(0, 0, 0, 0.65)',
          backdropFilter: 'blur(4px)',
        }}
      />

      {/* Drawer Container */}
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby="knowledge-drawer-title"
        tabIndex={-1}
        onKeyDown={onKeyDown}
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
            padding: compact ? '1rem' : '1.25rem 1.5rem',
            borderBottom: '1px solid var(--app-border)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '0.75rem',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem', minWidth: 0 }}>
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
                flexShrink: 0,
              }}
            >
              <Database size={17} />
            </div>
            <div style={{ minWidth: 0 }}>
              <h3 id="knowledge-drawer-title" style={{ fontSize: '1.05rem', fontWeight: 700 }}>
                Cloud Drive Knowledge Base
              </h3>
              <span style={{ fontSize: '0.75rem', color: 'var(--app-text-muted)' }}>
                OneDrive & Google Drive documents indexed in pgvector
              </span>
            </div>
          </div>

          <button
            type="button"
            onClick={onClose}
            style={{
              background: 'transparent',
              border: 'none',
              color: 'var(--app-text-muted)',
              cursor: 'pointer',
              padding: '6px',
              borderRadius: '6px',
              display: 'flex',
              alignItems: 'center',
              flexShrink: 0,
            }}
            title="Close drawer"
            aria-label="Close knowledge base"
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
            flexWrap: 'wrap',
            gap: '0.5rem',
            padding: compact ? '0.6rem 1rem' : '0.75rem 1.5rem',
            borderBottom: '1px solid var(--app-border)',
            background: 'var(--app-sidebar-hover)',
          }}
        >
          <button type="button" onClick={() => onTabChange('browse')} aria-pressed={tab === 'browse'} style={tabStyle(tab === 'browse')}>
            <FolderSync size={15} />
            <span>Browse & Index Cloud Drives</span>
          </button>

          <button type="button" onClick={() => onTabChange('indexed')} aria-pressed={tab === 'indexed'} style={tabStyle(tab === 'indexed')}>
            <Database size={15} />
            <span>Indexed Documents & Chunks</span>
          </button>
        </div>

        {/* Drawer Body Scroll View */}
        <div
          style={{
            flex: 1,
            overflowY: 'auto',
            padding: compact ? '1rem' : '1.5rem',
          }}
        >
          {children}
        </div>
      </div>
    </div>
  );
}

export default App;
