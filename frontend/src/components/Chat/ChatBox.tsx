import { memo, useCallback, useEffect, useRef, useState } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent } from 'react';
import {
  ArrowUp,
  ThumbsUp,
  ThumbsDown,
  Check,
  Copy,
  FolderSync,
  Database,
  StopCircle,
  Bot,
  Sparkles,
  PanelLeftOpen,
  Sun,
  Moon,
  RefreshCw,
  AlertCircle,
  X,
} from 'lucide-react';
import { ChatMessage, Citation } from '../../types';
import { ApiError, SessionMessage, apiFetch, errorFrom, fetchSessionMessages, sendFeedback } from '../../services/api';
import { MOBILE_QUERY, useMediaQuery } from '../../hooks';
import { AnswerMarkdown } from './AnswerMarkdown';

interface ChatBoxProps {
  sessionId: string | null;
  onSessionChange: (newSessionId: string, firstQuery?: string) => void;
  /** Called when an answer stream ends, so the chat list can pick up the new title and time. */
  onAnswerComplete?: () => void;
  onOpenKnowledgeDrawer: () => void;
  userName?: string;
  isSidebarOpen?: boolean;
  onOpenSidebar?: () => void;
  theme?: 'dark' | 'light';
  onToggleTheme?: () => void;
}

interface StreamEvent {
  status?: 'searching' | 'generating' | 'reading';
  parts?: number;
  session_id?: string;
  token?: string;
  done?: boolean;
  error?: string;
  message_id?: string;
  citations?: Citation[];
}

/** The answer being generated: its render key and the latest status event. */
interface PendingAnswer {
  key: string;
  status: string;
  parts?: number;
}

type LoadState = { status: 'idle' } | { status: 'loading' } | { status: 'error'; message: string };

interface SendError {
  text: string;
  kind: 'error' | 'warning';
}

const STATUS_TEXT: Record<string, string> = {
  searching: 'Searching your documents...',
  generating: 'Writing the answer...',
  reading: 'Reading the whole document...',
};

/** Same limit as the backend (MAX_QUERY_CHARS). */
const MAX_QUERY_CHARS = 4000;
/** Auto-scroll only follows new text while the reader is this close to the bottom. */
const NEAR_BOTTOM_PX = 120;
/** On touch devices focusing the input would pop up the on-screen keyboard over the answer. */
const COARSE_POINTER = '(pointer: coarse)';

function statusLabel(status: string, parts?: number): string {
  const text = STATUS_TEXT[status] || STATUS_TEXT.searching;
  return status === 'reading' && parts ? `${text} (${parts} parts)` : text;
}

const withNote = (content: string, note: string) => (content ? `${content}\n\n_${note}_` : `_${note}_`);

const toChatMessage = (m: SessionMessage): ChatMessage => ({
  id: m.id,
  role: m.role,
  content: m.content,
  citations: m.citations ?? [],
  feedback: m.feedback,
  timestamp: m.created_at ?? '',
  persisted: true,
});

function describeSendError(e: unknown): SendError {
  if (e instanceof ApiError) {
    // Rate limits come with a friendly message from the backend; show it as-is.
    if (e.status === 429) return { text: e.message, kind: 'warning' };
    return { text: `Could not send your question: ${e.message}`, kind: 'error' };
  }
  return { text: 'Could not reach the server. Check your connection and try again.', kind: 'error' };
}

/** Copies text; resolves false when the browser refuses (no Clipboard API outside https, or permission denied). */
async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Fall through to the legacy path.
  }
  const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  area.style.position = 'fixed';
  area.style.opacity = '0';
  document.body.appendChild(area);
  try {
    area.select();
    return document.execCommand('copy');
  } catch {
    return false;
  } finally {
    area.remove();
    opener?.focus();
  }
}

/** Parses a Server-Sent Events stream, handling events split across network chunks. */
async function readEvents(body: ReadableStream<Uint8Array>, onEvent: (event: StreamEvent) => void) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf('\n\n');
    while (boundary !== -1) {
      const raw = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const data = raw
        .split('\n')
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).trimStart())
        .join('\n');
      if (data) onEvent(JSON.parse(data));
      boundary = buffer.indexOf('\n\n');
    }
  }
}

interface MessageRowProps {
  message: ChatMessage;
  /** Status text of the answer being generated; only the in-flight message gets it. */
  pendingStatus?: string;
  copyResult?: 'copied' | 'failed';
  onCopy: (text: string, messageId: string) => void;
  onFeedback: (messageId: string, score: 1 | -1) => void;
}

/** One message; memoised so a streamed token only re-renders the message it belongs to. */
const MessageRow = memo(function MessageRow({ message: msg, pendingStatus, copyResult, onCopy, onFeedback }: MessageRowProps) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '0.5rem',
      }}
      className="animate-fade-in"
    >
      {/* User Message */}
      {msg.role === 'user' ? (
        <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
          <div
            style={{
              maxWidth: '82%',
              background: 'var(--user-msg-bg)',
              border: '1px solid var(--user-msg-border)',
              borderRadius: '20px',
              padding: '0.9rem 1.25rem',
              color: 'var(--app-text)',
              fontSize: '0.95rem',
              lineHeight: '1.6',
              boxShadow: 'var(--card-shadow)',
              whiteSpace: 'pre-wrap',
              overflowWrap: 'anywhere',
            }}
          >
            {msg.content}
          </div>
        </div>
      ) : (
        /* Assistant Message */
        <div style={{ display: 'flex', gap: '1rem', alignItems: 'flex-start' }}>
          {/* Logo Avatar: Electric Blue / Cyan Gradient */}
          <div
            style={{
              width: '34px',
              height: '34px',
              borderRadius: '10px',
              background: 'var(--brand-gradient)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#ffffff',
              flexShrink: 0,
              marginTop: '2px',
              boxShadow: '0 4px 12px var(--brand-glow)',
            }}
          >
            <Bot size={18} />
          </div>

          <div style={{ flex: 1, minWidth: 0 }}>
            {/* Header name */}
            <div
              style={{
                fontSize: '0.85rem',
                fontWeight: 700,
                color: 'var(--brand-cyan)',
                marginBottom: '0.4rem',
              }}
            >
              Enterprise Cloud RAG
            </div>

            {/* Content */}
            <div className="prose">
              {msg.content ? (
                <div className={msg.isError ? 'answer-error' : undefined}>
                  <AnswerMarkdown content={msg.content} citations={msg.citations} />
                </div>
              ) : pendingStatus ? (
                <div role="status" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--app-text-muted)' }}>
                  <Sparkles size={16} color="var(--brand-cyan)" className="animate-spin" aria-hidden="true" />
                  <span>{pendingStatus}</span>
                </div>
              ) : null}
            </div>

            {/* Action buttons (Copy, Feedback) */}
            {msg.content && (
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.75rem',
                  marginTop: '0.85rem',
                  color: 'var(--app-text-muted)',
                  fontSize: '0.75rem',
                }}
              >
                <button
                  type="button"
                  onClick={() => onCopy(msg.content, msg.id)}
                  style={{
                    background: 'transparent',
                    border: 'none',
                    color: 'var(--app-text-muted)',
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.3rem',
                    padding: '2px 4px',
                    borderRadius: '4px',
                  }}
                  title="Copy response"
                >
                  {copyResult === 'copied' ? (
                    <>
                      <Check size={13} color="var(--success-text)" />
                      <span style={{ color: 'var(--success-text)' }}>Copied</span>
                    </>
                  ) : copyResult === 'failed' ? (
                    <>
                      <AlertCircle size={13} color="var(--danger-text)" />
                      <span style={{ color: 'var(--danger-text)' }}>Copy failed</span>
                    </>
                  ) : (
                    <>
                      <Copy size={13} />
                      <span>Copy</span>
                    </>
                  )}
                </button>

                {msg.persisted && (
                  <>
                    <button
                      type="button"
                      onClick={() => onFeedback(msg.id, 1)}
                      aria-pressed={msg.feedback === 1}
                      aria-label="Helpful answer"
                      style={{
                        background: msg.feedback === 1 ? 'rgba(16,185,129,0.15)' : 'transparent',
                        border: 'none',
                        color: msg.feedback === 1 ? 'var(--success-text)' : 'var(--app-text-muted)',
                        cursor: 'pointer',
                        padding: '2px 4px',
                        borderRadius: '4px',
                      }}
                      title="Helpful answer"
                    >
                      <ThumbsUp size={13} />
                    </button>
                    <button
                      type="button"
                      onClick={() => onFeedback(msg.id, -1)}
                      aria-pressed={msg.feedback === -1}
                      aria-label="Not helpful"
                      style={{
                        background: msg.feedback === -1 ? 'rgba(244,63,94,0.15)' : 'transparent',
                        border: 'none',
                        color: msg.feedback === -1 ? 'var(--danger-text)' : 'var(--app-text-muted)',
                        cursor: 'pointer',
                        padding: '2px 4px',
                        borderRadius: '4px',
                      }}
                      title="Not helpful"
                    >
                      <ThumbsDown size={13} />
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
});

export function ChatBox({
  sessionId,
  onSessionChange,
  onAnswerComplete,
  onOpenKnowledgeDrawer,
  userName = 'there',
  isSidebarOpen = true,
  onOpenSidebar,
  theme = 'dark',
  onToggleTheme,
}: ChatBoxProps) {
  const isMobile = useMediaQuery(MOBILE_QUERY);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputQuery, setInputQuery] = useState<string>('');
  const [pending, setPending] = useState<PendingAnswer | null>(null);
  const [loadState, setLoadState] = useState<LoadState>({ status: 'idle' });
  const [sendError, setSendError] = useState<SendError | null>(null);
  const [copyState, setCopyState] = useState<{ id: string; result: 'copied' | 'failed' } | null>(null);

  const scrollRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef(true);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const inputBarRef = useRef<HTMLDivElement>(null);
  /** The in-flight question; null once it ends or when the user leaves the conversation. */
  const requestRef = useRef<{ controller: AbortController } | null>(null);
  const activeSessionIdRef = useRef<string | null>(null);
  /** Bumped on every load and every send, so a stale history load is ignored. */
  const loadEpochRef = useRef(0);
  const copyTimerRef = useRef<number | undefined>(undefined);
  const isStreaming = pending !== null;

  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickToBottomRef.current) el.scrollTop = el.scrollHeight;
  }, [messages, pending]);

  function handleScroll() {
    const el = scrollRef.current;
    if (el) stickToBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX;
  }

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 180)}px`;
    }
  }, [inputQuery]);

  useEffect(() => {
    if (!window.matchMedia(COARSE_POINTER).matches) textareaRef.current?.focus();
  }, [sessionId]);

  useEffect(
    () => () => {
      requestRef.current?.controller.abort();
      requestRef.current = null;
      window.clearTimeout(copyTimerRef.current);
    },
    []
  );

  const loadSession = useCallback((id: string) => {
    const epoch = ++loadEpochRef.current;
    setLoadState({ status: 'loading' });
    fetchSessionMessages(id).then(
      (data) => {
        if (epoch !== loadEpochRef.current) return; // switched again, or a new question was already sent
        stickToBottomRef.current = true;
        setMessages(data.messages.map(toChatMessage));
        setLoadState({ status: 'idle' });
      },
      (e: unknown) => {
        if (epoch !== loadEpochRef.current) return;
        setLoadState({ status: 'error', message: (e as Error).message || 'Unknown error' });
      }
    );
  }, []);

  // Load the selected session (skipped when the session id was just assigned by our own stream).
  useEffect(() => {
    if (activeSessionIdRef.current === sessionId) return;
    activeSessionIdRef.current = sessionId;
    const request = requestRef.current;
    requestRef.current = null;
    request?.controller.abort();
    setPending(null);
    setMessages([]);
    setSendError(null);
    stickToBottomRef.current = true;
    if (sessionId) {
      loadSession(sessionId);
    } else {
      loadEpochRef.current++;
      setLoadState({ status: 'idle' });
    }
  }, [sessionId, loadSession]);

  function adoptSession(id: string | null | undefined, firstQuery: string) {
    if (!id || id === activeSessionIdRef.current) return;
    activeSessionIdRef.current = id;
    onSessionChange(id, firstQuery);
  }

  /** Puts the cursor back in the input unless the user is busy elsewhere (e.g. in the drawer). */
  function refocusInput() {
    if (window.matchMedia(COARSE_POINTER).matches) return;
    const active = document.activeElement;
    if (!active || active === document.body || inputBarRef.current?.contains(active)) textareaRef.current?.focus();
  }

  async function handleSend(queryText?: string) {
    const q = (queryText ?? inputQuery).trim().slice(0, MAX_QUERY_CHARS);
    if (!q || requestRef.current) return;
    if (queryText === undefined) setInputQuery('');
    setSendError(null);
    loadEpochRef.current++; // a history load finishing now must not replace this exchange
    setLoadState({ status: 'idle' });

    const request = { controller: new AbortController() };
    requestRef.current = request;
    const isCurrent = () => requestRef.current === request;
    const now = new Date().toISOString();
    const userKey = `user_${Date.now()}`;
    const answerKey = `pending_${Date.now()}`;
    let answerId = answerKey;
    stickToBottomRef.current = true;
    setMessages((prev) => [
      ...prev,
      { id: userKey, clientId: userKey, role: 'user', content: q, timestamp: now },
      { id: answerKey, clientId: answerKey, role: 'assistant', content: '', timestamp: now },
    ]);
    setPending({ key: answerKey, status: 'searching' });

    let started = false; // the server accepted the question and is streaming
    let terminal = false; // a done or error event arrived
    let received = '';
    const patchAnswer = (patch: (m: ChatMessage) => Partial<ChatMessage>) => {
      const id = answerId;
      setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, ...patch(m) } : m)));
    };

    try {
      const response = await apiFetch('/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: q, session_id: activeSessionIdRef.current }),
        signal: request.controller.signal,
      });
      if (!isCurrent()) return;
      if (!response.ok || !response.body) throw await errorFrom(response);
      started = true;
      adoptSession(response.headers.get('X-Chat-Session-Id'), q);

      await readEvents(response.body, (event) => {
        if (!isCurrent()) return;
        if (event.session_id) adoptSession(event.session_id, q);
        if (event.status) {
          const { status, parts } = event;
          setPending((p) => (p ? { ...p, status, parts } : p));
        }
        if (event.token) {
          received += event.token;
          const text = received;
          patchAnswer(() => ({ content: text }));
        }
        if (event.error) {
          terminal = true;
          const error = event.error;
          const serverId = event.message_id;
          // A partial answer is saved under message_id, so adopting it lets feedback work.
          patchAnswer((m) => ({
            content: m.content ? `${m.content}\n\n_${error}_` : error,
            isError: !m.content,
            ...(serverId ? { id: serverId, persisted: Boolean(m.content) } : {}),
          }));
          if (serverId) answerId = serverId;
        } else if (event.done) {
          terminal = true;
          const serverId = event.message_id;
          const citations = event.citations ?? [];
          patchAnswer(() => ({ citations, ...(serverId ? { id: serverId, persisted: true } : {}) }));
          if (serverId) answerId = serverId;
        }
      });
      // The stream closed without done/error: the connection dropped mid-answer.
      if (isCurrent() && !terminal) {
        patchAnswer((m) => ({ content: withNote(m.content, '(Connection lost.)'), isError: !m.content }));
      }
    } catch (e: unknown) {
      if (!isCurrent() || terminal) return;
      if ((e as Error).name === 'AbortError') {
        patchAnswer((m) => ({ content: withNote(m.content, '(Stopped.)') }));
      } else if (!started) {
        // The question never reached the assistant: take it back so it can be sent again.
        setMessages((prev) => prev.filter((m) => m.id !== userKey && m.id !== answerKey));
        setInputQuery((current) => (current.trim() ? current : q));
        setSendError(describeSendError(e));
      } else {
        patchAnswer((m) => ({ content: withNote(m.content, '(Connection lost.)'), isError: !m.content }));
      }
    } finally {
      if (isCurrent()) {
        requestRef.current = null;
        setPending(null);
        if (started) onAnswerComplete?.();
        refocusInput();
      }
    }
  }

  function handleStopGeneration() {
    requestRef.current?.controller.abort();
  }

  const handleFeedback = useCallback(async (messageId: string, score: 1 | -1) => {
    try {
      await sendFeedback(messageId, score);
      setMessages((prev) => prev.map((m) => (m.id === messageId ? { ...m, feedback: score } : m)));
    } catch (e) {
      console.error('Failed to submit feedback', e);
    }
  }, []);

  const handleCopy = useCallback(async (text: string, messageId: string) => {
    const ok = await copyToClipboard(text);
    setCopyState({ id: messageId, result: ok ? 'copied' : 'failed' });
    window.clearTimeout(copyTimerRef.current);
    copyTimerRef.current = window.setTimeout(() => setCopyState(null), 2000);
  }, []);

  function handleKeyDown(e: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if (e.key !== 'Enter' || e.shiftKey) return;
    if (e.nativeEvent.isComposing || e.keyCode === 229) return; // an IME is composing (e.g. Japanese, Chinese)
    e.preventDefault();
    if (!isStreaming) handleSend();
  }

  const samplePrompts = [
    { title: 'What documents do I have?', subtitle: 'List everything that is indexed', query: 'Which documents do I have?' },
    { title: 'Summarize a document', subtitle: 'e.g. "Summarize Projects.docx"', query: 'Summarize my most recent document' },
    { title: 'Find a fact', subtitle: 'Numbers, dates, names, policies', query: 'What deadlines are mentioned in my documents?' },
    { title: 'Compare across files', subtitle: 'Answers cite every source', query: 'What topics appear in more than one of my documents?' },
  ];

  const canSend = inputQuery.trim().length > 0;
  const pendingStatus = pending ? statusLabel(pending.status, pending.parts) : undefined;

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        width: '100%',
        position: 'relative',
        background: 'var(--app-bg)',
      }}
    >
      {/* Top Navigation Bar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: '0.5rem 0.75rem',
          padding: isMobile ? '0.65rem 0.85rem' : '0.85rem 1.5rem',
          borderBottom: '1px solid var(--app-border)',
          background: 'var(--app-header-bg)',
          backdropFilter: 'blur(10px)',
          zIndex: 10,
        }}
      >
        {/* Left Section: Sidebar Open Toggle (when collapsed) + title */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', minWidth: 0 }}>
          {!isSidebarOpen && onOpenSidebar && (
            <button
              type="button"
              onClick={onOpenSidebar}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.45rem',
                padding: '0.4rem 0.75rem',
                background: 'var(--app-surface)',
                border: '1px solid var(--app-border)',
                borderRadius: 'var(--radius-md)',
                color: 'var(--app-text-secondary)',
                fontSize: '0.825rem',
                fontWeight: 600,
                cursor: 'pointer',
                transition: 'all 0.15s ease',
              }}
              title="Open sidebar"
              onMouseEnter={(e) => {
                e.currentTarget.style.color = 'var(--app-text)';
                e.currentTarget.style.borderColor = 'var(--brand-primary)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.color = 'var(--app-text-secondary)';
                e.currentTarget.style.borderColor = 'var(--app-border)';
              }}
            >
              <PanelLeftOpen size={16} />
              <span style={{ color: 'var(--brand-cyan)', fontWeight: 700 }}>Enterprise Cloud RAG</span>
            </button>
          )}

          <span style={{ fontSize: '0.9rem', fontWeight: 700, color: 'var(--app-text)' }}>Document Assistant</span>
        </div>

        {/* Right Section: Theme Toggle + Knowledge Base Drawer */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap' }}>
          {onToggleTheme && (
            <button
              type="button"
              onClick={onToggleTheme}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.45rem',
                padding: '0.4rem 0.8rem',
                background: 'var(--app-surface)',
                border: '1px solid var(--app-border)',
                borderRadius: 'var(--radius-full)',
                color: 'var(--app-text)',
                fontSize: '0.78rem',
                fontWeight: 600,
                cursor: 'pointer',
                transition: 'all 0.15s ease',
              }}
              title={`Switch to ${theme === 'dark' ? 'Light' : 'Dark'} mode`}
              onMouseEnter={(e) => {
                e.currentTarget.style.borderColor = 'var(--brand-primary)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.borderColor = 'var(--app-border)';
              }}
            >
              {theme === 'dark' ? (
                <>
                  <Sun size={14} color="#f59e0b" />
                  <span>Light</span>
                </>
              ) : (
                <>
                  <Moon size={14} color="#0284c7" />
                  <span>Dark</span>
                </>
              )}
            </button>
          )}

          <button
            type="button"
            onClick={onOpenKnowledgeDrawer}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.45rem',
              padding: '0.4rem 0.85rem',
              background: 'var(--app-surface)',
              border: '1px solid var(--app-border)',
              borderRadius: 'var(--radius-full)',
              color: 'var(--app-text-secondary)',
              fontSize: '0.78rem',
              fontWeight: 600,
              cursor: 'pointer',
              transition: 'all 0.15s ease',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.borderColor = 'var(--brand-primary)';
              e.currentTarget.style.color = 'var(--brand-cyan)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.borderColor = 'var(--app-border)';
              e.currentTarget.style.color = 'var(--app-text-secondary)';
            }}
          >
            <Database size={13} color="var(--brand-cyan)" />
            <span>Knowledge & Cloud Drive</span>
          </button>
        </div>
      </div>

      {/* Main Conversation Stream Viewport */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        style={{
          flex: 1,
          overflowY: 'auto',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          padding: isMobile ? '1rem 0.75rem 8rem 0.75rem' : '1.5rem 1rem 8rem 1rem',
        }}
      >
        <div style={{ width: '100%', maxWidth: '780px' }}>
          {messages.length === 0 && loadState.status === 'loading' ? (
            <div
              role="status"
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '0.5rem',
                minHeight: '40vh',
                color: 'var(--app-text-muted)',
                fontSize: '0.9rem',
              }}
            >
              <RefreshCw size={16} className="animate-spin" aria-hidden="true" />
              <span>Loading conversation...</span>
            </div>
          ) : messages.length === 0 && loadState.status === 'error' ? (
            <div
              role="alert"
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '1rem',
                minHeight: '40vh',
                textAlign: 'center',
              }}
            >
              <span style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--danger-text)', fontSize: '0.9rem' }}>
                <AlertCircle size={16} aria-hidden="true" />
                Could not load this conversation: {loadState.message}
              </span>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => activeSessionIdRef.current && loadSession(activeSessionIdRef.current)}
              >
                <RefreshCw size={14} /> Try again
              </button>
            </div>
          ) : messages.length === 0 ? (
            /* Empty State / Welcome Screen */
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                minHeight: '52vh',
                textAlign: 'center',
                padding: '2rem 1rem',
              }}
              className="animate-fade-in"
            >
              {/* Previous Logo in Welcome Header */}
              <div
                style={{
                  width: '60px',
                  height: '60px',
                  borderRadius: '16px',
                  background: 'var(--brand-gradient)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  boxShadow: '0 8px 28px var(--brand-glow)',
                  marginBottom: '1.25rem',
                }}
              >
                <FolderSync size={32} color="#ffffff" />
              </div>

              <h2
                style={{
                  fontSize: '1.8rem',
                  fontWeight: 800,
                  letterSpacing: '-0.02em',
                  marginBottom: '0.4rem',
                  color: 'var(--app-text)',
                }}
              >
                Enterprise Cloud RAG
              </h2>

              <p
                style={{
                  fontSize: '0.925rem',
                  color: 'var(--app-text-secondary)',
                  maxWidth: '480px',
                  lineHeight: '1.6',
                  marginBottom: '2.5rem',
                }}
              >
                Welcome, {userName}. Ask questions about your synced OneDrive and Google Drive files. Every answer links to the passages it is based on.
              </p>

              {/* Sample Prompt Cards */}
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fit, minmax(min(280px, 100%), 1fr))',
                  gap: '0.85rem',
                  width: '100%',
                  textAlign: 'left',
                }}
              >
                {samplePrompts.map((prompt) => (
                  <button
                    key={prompt.query}
                    type="button"
                    onClick={() => handleSend(prompt.query)}
                    style={{
                      width: '100%',
                      height: '100%',
                      padding: '1rem 1.15rem',
                      background: 'var(--app-surface)',
                      border: '1px solid var(--app-border)',
                      borderRadius: 'var(--radius-lg)',
                      cursor: 'pointer',
                      transition: 'all 0.18s ease',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '0.25rem',
                      textAlign: 'left',
                      fontFamily: 'inherit',
                      lineHeight: 'inherit',
                      color: 'inherit',
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.borderColor = 'var(--brand-primary)';
                      e.currentTarget.style.background = 'var(--app-surface-hover)';
                      e.currentTarget.style.boxShadow = '0 4px 16px var(--brand-glow)';
                      e.currentTarget.style.transform = 'translateY(-2px)';
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.borderColor = 'var(--app-border)';
                      e.currentTarget.style.background = 'var(--app-surface)';
                      e.currentTarget.style.boxShadow = 'none';
                      e.currentTarget.style.transform = 'translateY(0)';
                    }}
                  >
                    <span style={{ fontSize: '0.875rem', fontWeight: 600, color: 'var(--app-text)' }}>
                      {prompt.title}
                    </span>
                    <span style={{ fontSize: '0.75rem', color: 'var(--app-text-muted)' }}>
                      {prompt.subtitle}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            /* Active Message History */
            <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
              {messages.map((msg) => {
                const key = msg.clientId ?? msg.id;
                return (
                  <MessageRow
                    key={key}
                    message={msg}
                    pendingStatus={pending?.key === key ? pendingStatus : undefined}
                    copyResult={copyState?.id === msg.id ? copyState.result : undefined}
                    onCopy={handleCopy}
                    onFeedback={handleFeedback}
                  />
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Bottom Floating Input Bar */}
      <div
        style={{
          position: 'absolute',
          bottom: 0,
          left: 0,
          right: 0,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          padding: isMobile ? '0.5rem 0.75rem 0.75rem 0.75rem' : '0.75rem 1.5rem 1.25rem 1.5rem',
          background: 'linear-gradient(to top, var(--app-bg) 75%, transparent 100%)',
          zIndex: 20,
        }}
      >
        {sendError && (
          <div
            role="alert"
            style={{
              width: '100%',
              maxWidth: '780px',
              display: 'flex',
              alignItems: 'center',
              gap: '0.5rem',
              padding: '0.55rem 0.85rem',
              marginBottom: '0.5rem',
              borderRadius: 'var(--radius-md)',
              background: sendError.kind === 'warning' ? 'var(--warning-bg)' : 'var(--danger-bg)',
              border: `1px solid ${sendError.kind === 'warning' ? 'var(--warning-border)' : 'var(--danger-border)'}`,
              color: sendError.kind === 'warning' ? 'var(--warning-text)' : 'var(--danger-text)',
              fontSize: '0.8rem',
            }}
          >
            <AlertCircle size={14} style={{ flexShrink: 0 }} aria-hidden="true" />
            <span style={{ flex: 1 }}>{sendError.text}</span>
            <button
              type="button"
              onClick={() => setSendError(null)}
              aria-label="Dismiss message"
              style={{ background: 'transparent', border: 'none', color: 'inherit', cursor: 'pointer', display: 'flex', padding: '2px' }}
            >
              <X size={14} />
            </button>
          </div>
        )}

        <div
          ref={inputBarRef}
          className="chat-input"
          style={{
            width: '100%',
            maxWidth: '780px',
            background: 'var(--app-input-bg)',
            borderRadius: 'var(--radius-xl)',
            padding: '0.75rem 1rem',
            display: 'flex',
            flexDirection: 'column',
            gap: '0.5rem',
          }}
        >
          {/* Multiline auto-expanding textarea */}
          <textarea
            ref={textareaRef}
            rows={1}
            value={inputQuery}
            maxLength={MAX_QUERY_CHARS}
            onChange={(e) => setInputQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask a question about your OneDrive or Google Drive documents..."
            aria-label="Ask a question about your documents"
            style={{
              width: '100%',
              background: 'transparent',
              border: 'none',
              outline: 'none',
              resize: 'none',
              color: 'var(--app-text)',
              fontSize: '0.95rem',
              lineHeight: '1.5',
              fontFamily: 'var(--font-sans)',
              maxHeight: '180px',
            }}
          />

          {/* Input Bottom Toolbar */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              paddingTop: '0.25rem',
            }}
          >
            {/* Left side: Context badge */}
            <button
              type="button"
              onClick={onOpenKnowledgeDrawer}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '0.35rem',
                padding: '0.25rem 0.65rem',
                background: 'var(--brand-bg)',
                border: '1px solid var(--app-border)',
                borderRadius: 'var(--radius-full)',
                color: 'var(--app-text-secondary)',
                fontSize: '0.75rem',
                fontWeight: 600,
                cursor: 'pointer',
                transition: 'all 0.15s ease',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.borderColor = 'var(--brand-primary)';
                e.currentTarget.style.color = 'var(--info-text)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.borderColor = 'var(--app-border)';
                e.currentTarget.style.color = 'var(--app-text-secondary)';
              }}
              title="Browse and index OneDrive and Google Drive files"
            >
              <FolderSync size={13} color="var(--brand-cyan)" />
              <span>Manage documents</span>
            </button>

            {/* Right side: Send or Stop button */}
            {isStreaming ? (
              <button
                type="button"
                onClick={handleStopGeneration}
                style={{
                  width: '32px',
                  height: '32px',
                  borderRadius: '50%',
                  background: 'rgba(244, 63, 94, 0.2)',
                  border: '1px solid rgba(244, 63, 94, 0.4)',
                  color: 'var(--danger-text)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  cursor: 'pointer',
                }}
                title="Stop generating"
                aria-label="Stop generating"
              >
                <StopCircle size={16} />
              </button>
            ) : (
              <button
                type="button"
                onClick={() => handleSend()}
                disabled={!canSend}
                style={{
                  width: '32px',
                  height: '32px',
                  borderRadius: '50%',
                  background: canSend ? 'var(--brand-gradient)' : 'rgba(255, 255, 255, 0.08)',
                  border: 'none',
                  color: canSend ? '#ffffff' : 'var(--app-text-muted)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  cursor: canSend ? 'pointer' : 'default',
                  transition: 'all 0.15s ease',
                  boxShadow: canSend ? '0 2px 10px var(--brand-glow)' : 'none',
                }}
                title="Send query"
                aria-label="Send question"
              >
                <ArrowUp size={18} strokeWidth={2.5} />
              </button>
            )}
          </div>
        </div>

        {/* Footnote */}
        <span
          style={{
            fontSize: '0.68rem',
            color: 'var(--app-text-muted)',
            marginTop: '0.5rem',
            letterSpacing: '0.01em',
            textAlign: 'center',
          }}
        >
          Answers come only from your indexed files and can still contain mistakes. Check the linked sources.
        </span>
      </div>
    </div>
  );
}

export default ChatBox;
