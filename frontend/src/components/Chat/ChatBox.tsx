import React, { useState, useRef, useEffect } from 'react';
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
} from 'lucide-react';
import { ChatMessage, Citation } from '../../types';
import { apiFetch, errorFrom, fetchSessionMessages, sendFeedback } from '../../services/api';
import { AnswerMarkdown } from './AnswerMarkdown';

interface ChatBoxProps {
  sessionId: string | null;
  onSessionChange: (newSessionId: string, firstQuery?: string) => void;
  onOpenKnowledgeDrawer: () => void;
  userName?: string;
  isSidebarOpen?: boolean;
  onOpenSidebar?: () => void;
  theme?: 'dark' | 'light';
  onToggleTheme?: () => void;
}

interface StreamEvent {
  status?: 'searching' | 'generating';
  token?: string;
  done?: boolean;
  error?: string;
  message_id?: string;
  citations?: Citation[];
}

const STATUS_TEXT: Record<string, string> = {
  searching: 'Searching your documents...',
  generating: 'Writing the answer...',
};

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

export function ChatBox({
  sessionId,
  onSessionChange,
  onOpenKnowledgeDrawer,
  userName = 'there',
  isSidebarOpen = true,
  onOpenSidebar,
  theme = 'dark',
  onToggleTheme,
}: ChatBoxProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputQuery, setInputQuery] = useState<string>('');
  const [isStreaming, setIsStreaming] = useState<boolean>(false);
  const [streamStatus, setStreamStatus] = useState<string>('searching');
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const activeSessionIdRef = useRef<string | null>(sessionId);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isStreaming]);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 180)}px`;
    }
  }, [inputQuery]);

  useEffect(() => {
    textareaRef.current?.focus();
  }, [sessionId]);

  // Load the selected session (skipped when the session id was just assigned by our own stream).
  useEffect(() => {
    if (activeSessionIdRef.current === sessionId) return;
    activeSessionIdRef.current = sessionId;
    abortControllerRef.current?.abort();
    if (!sessionId) {
      setMessages([]);
      return;
    }
    let cancelled = false;
    fetchSessionMessages(sessionId)
      .then((data) => {
        if (cancelled) return;
        setMessages(
          data.messages.map((m) => ({
            id: m.id,
            role: m.role,
            content: m.content,
            citations: m.citations || [],
            feedback: m.feedback,
            timestamp: m.created_at,
            persisted: true,
          }))
        );
      })
      .catch((e) => console.error('Failed to load session messages', e));
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  function updateMessage(id: string, patch: (m: ChatMessage) => Partial<ChatMessage>) {
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, ...patch(m) } : m)));
  }

  async function handleSend(queryText?: string) {
    const q = (queryText ?? inputQuery).trim();
    if (!q || isStreaming) return;

    setInputQuery('');
    const now = new Date().toISOString();
    let assistantId = `pending_${Date.now()}`;
    setMessages((prev) => [
      ...prev,
      { id: `user_${Date.now()}`, role: 'user', content: q, timestamp: now },
      { id: assistantId, role: 'assistant', content: '', timestamp: now },
    ]);
    setIsStreaming(true);
    setStreamStatus('searching');

    const controller = new AbortController();
    abortControllerRef.current = controller;
    let received = '';

    try {
      const response = await apiFetch('/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: q, session_id: activeSessionIdRef.current }),
        signal: controller.signal,
      });
      if (!response.ok || !response.body) throw await errorFrom(response);

      const newSessionId = response.headers.get('X-Chat-Session-Id');
      if (newSessionId && newSessionId !== activeSessionIdRef.current) {
        activeSessionIdRef.current = newSessionId;
        onSessionChange(newSessionId, q);
      }

      await readEvents(response.body, (event) => {
        if (event.status) setStreamStatus(event.status);
        if (event.token) {
          received += event.token;
          const text = received;
          updateMessage(assistantId, () => ({ content: text }));
        }
        if (event.error) {
          updateMessage(assistantId, (m) => ({
            content: m.content ? `${m.content}\n\n_${event.error}_` : event.error!,
            isError: !m.content,
          }));
        } else if (event.done && event.message_id) {
          const serverId = event.message_id;
          updateMessage(assistantId, () => ({ id: serverId, citations: event.citations || [], persisted: true }));
          assistantId = serverId;
        }
      });
    } catch (e: unknown) {
      const err = e as Error;
      if (err.name === 'AbortError') {
        updateMessage(assistantId, (m) => ({ content: m.content ? `${m.content}\n\n_(Stopped.)_` : '_(Stopped.)_' }));
      } else {
        updateMessage(assistantId, (m) => ({
          content: m.content || `Unable to get an answer: ${err.message}`,
          isError: !m.content,
        }));
      }
    } finally {
      setIsStreaming(false);
      abortControllerRef.current = null;
    }
  }

  function handleStopGeneration() {
    abortControllerRef.current?.abort();
  }

  async function handleFeedback(messageId: string, score: 1 | -1) {
    try {
      await sendFeedback(messageId, score);
      updateMessage(messageId, () => ({ feedback: score }));
    } catch (e) {
      console.error('Failed to submit feedback', e);
    }
  }

  function handleCopy(text: string, msgId: string) {
    navigator.clipboard.writeText(text);
    setCopiedId(msgId);
    setTimeout(() => setCopiedId(null), 2000);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  const samplePrompts = [
    { title: 'What documents do I have?', subtitle: 'List everything that is indexed', query: 'Which documents do I have?' },
    { title: 'Summarize a document', subtitle: 'e.g. "Summarize Projects.docx"', query: 'Summarize my most recent document' },
    { title: 'Find a fact', subtitle: 'Numbers, dates, names, policies', query: 'What deadlines are mentioned in my documents?' },
    { title: 'Compare across files', subtitle: 'Answers cite every source', query: 'What topics appear in more than one of my documents?' },
  ];

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
          padding: '0.85rem 1.5rem',
          borderBottom: '1px solid var(--app-border)',
          background: 'var(--app-header-bg)',
          backdropFilter: 'blur(10px)',
          zIndex: 10,
        }}
      >
        {/* Left Section: Sidebar Open Toggle (when collapsed) + title */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          {!isSidebarOpen && onOpenSidebar && (
            <button
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
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
          {onToggleTheme && (
            <button
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
        style={{
          flex: 1,
          overflowY: 'auto',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          padding: '1.5rem 1rem 8rem 1rem',
        }}
      >
        <div style={{ width: '100%', maxWidth: '780px' }}>
          {/* Empty State / Welcome Screen */}
          {messages.length === 0 ? (
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
                  gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
                  gap: '0.85rem',
                  width: '100%',
                  textAlign: 'left',
                }}
              >
                {samplePrompts.map((prompt, idx) => (
                  <div
                    key={idx}
                    onClick={() => handleSend(prompt.query)}
                    style={{
                      padding: '1rem 1.15rem',
                      background: 'var(--app-surface)',
                      border: '1px solid var(--app-border)',
                      borderRadius: 'var(--radius-lg)',
                      cursor: 'pointer',
                      transition: 'all 0.18s ease',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '0.25rem',
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
                  </div>
                ))}
              </div>
            </div>
          ) : (
            /* Active Message History */
            <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
              {messages.map((msg) => (
                <div
                  key={msg.id}
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
                            <div style={msg.isError ? { color: '#fb7185' } : undefined}>
                              <AnswerMarkdown content={msg.content} citations={msg.citations} />
                            </div>
                          ) : (
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--app-text-muted)' }}>
                              <Sparkles size={16} color="var(--brand-cyan)" className="animate-spin" />
                              <span>{STATUS_TEXT[streamStatus] || STATUS_TEXT.searching}</span>
                            </div>
                          )}
                        </div>

                        {/* Action buttons (Copy, Feedback) */}
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
                            onClick={() => handleCopy(msg.content, msg.id)}
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
                            {copiedId === msg.id ? (
                              <>
                                <Check size={13} color="#34d399" />
                                <span style={{ color: '#34d399' }}>Copied</span>
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
                                onClick={() => handleFeedback(msg.id, 1)}
                                style={{
                                  background: msg.feedback === 1 ? 'rgba(16,185,129,0.15)' : 'transparent',
                                  border: 'none',
                                  color: msg.feedback === 1 ? '#34d399' : 'var(--app-text-muted)',
                                  cursor: 'pointer',
                                  padding: '2px 4px',
                                  borderRadius: '4px',
                                }}
                                title="Helpful answer"
                              >
                                <ThumbsUp size={13} />
                              </button>
                              <button
                                onClick={() => handleFeedback(msg.id, -1)}
                                style={{
                                  background: msg.feedback === -1 ? 'rgba(244,63,94,0.15)' : 'transparent',
                                  border: 'none',
                                  color: msg.feedback === -1 ? '#fb7185' : 'var(--app-text-muted)',
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
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
          <div ref={messagesEndRef} />
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
          padding: '0.75rem 1.5rem 1.25rem 1.5rem',
          background: 'linear-gradient(to top, var(--app-bg) 75%, transparent 100%)',
          zIndex: 20,
        }}
      >
        <div
          style={{
            width: '100%',
            maxWidth: '780px',
            background: 'var(--app-input-bg)',
            border: '1px solid var(--app-border)',
            borderRadius: 'var(--radius-xl)',
            boxShadow: 'var(--input-shadow)',
            padding: '0.75rem 1rem',
            display: 'flex',
            flexDirection: 'column',
            gap: '0.5rem',
            transition: 'border-color 0.15s ease, box-shadow 0.15s ease',
          }}
        >
          {/* Multiline auto-expanding textarea */}
          <textarea
            ref={textareaRef}
            rows={1}
            value={inputQuery}
            onChange={(e) => setInputQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask a question about your OneDrive or Google Drive documents..."
            disabled={isStreaming}
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
                e.currentTarget.style.color = '#38bdf8';
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
                onClick={handleStopGeneration}
                style={{
                  width: '32px',
                  height: '32px',
                  borderRadius: '50%',
                  background: 'rgba(244, 63, 94, 0.2)',
                  border: '1px solid rgba(244, 63, 94, 0.4)',
                  color: '#fb7185',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  cursor: 'pointer',
                }}
                title="Stop generating"
              >
                <StopCircle size={16} />
              </button>
            ) : (
              <button
                onClick={() => handleSend()}
                disabled={!inputQuery.trim()}
                style={{
                  width: '32px',
                  height: '32px',
                  borderRadius: '50%',
                  background: inputQuery.trim() ? 'var(--brand-gradient)' : 'rgba(255, 255, 255, 0.08)',
                  border: 'none',
                  color: inputQuery.trim() ? '#ffffff' : 'var(--app-text-muted)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  cursor: inputQuery.trim() ? 'pointer' : 'default',
                  transition: 'all 0.15s ease',
                  boxShadow: inputQuery.trim() ? '0 2px 10px var(--brand-glow)' : 'none',
                }}
                title="Send query"
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
          }}
        >
          Answers come only from your indexed files and can still contain mistakes. Check the linked sources.
        </span>
      </div>
    </div>
  );
}

export default ChatBox;
