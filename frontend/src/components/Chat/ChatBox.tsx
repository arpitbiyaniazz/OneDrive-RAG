import React, { useState, useRef, useEffect } from 'react';
import {
  ArrowUp,
  ExternalLink,
  ThumbsUp,
  ThumbsDown,
  FileText,
  Check,
  Copy,
  FolderSync,
  Database,
  ChevronDown,
  StopCircle,
  Bot,
  Sparkles,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import { ChatMessage, Citation } from '../../types';

interface ChatBoxProps {
  sessionId: string | null;
  onSessionChange: (newSessionId: string, firstQuery?: string) => void;
  onOpenKnowledgeDrawer: () => void;
  userName?: string;
}

export function ChatBox({
  sessionId,
  onSessionChange,
  onOpenKnowledgeDrawer,
  userName = 'there',
}: ChatBoxProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputQuery, setInputQuery] = useState<string>('');
  const [isStreaming, setIsStreaming] = useState<boolean>(false);
  const [feedbackSent, setFeedbackSent] = useState<Record<string, number>>({});
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [selectedModel, setSelectedModel] = useState<string>('Enterprise RAG (GPT-4o Mini & Hybrid)');
  const [isModelDropdownOpen, setIsModelDropdownOpen] = useState<boolean>(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  // Auto-scroll on new message / token stream
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isStreaming]);

  // Adjust textarea height dynamically
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 180)}px`;
    }
  }, [inputQuery]);

  // Focus textarea on load / new session
  useEffect(() => {
    textareaRef.current?.focus();
  }, [sessionId]);

  // Reset messages when session ID changes to null (new chat)
  useEffect(() => {
    if (!sessionId) {
      setMessages([]);
    }
  }, [sessionId]);

  async function handleSend(queryText?: string) {
    const q = queryText || inputQuery;
    if (!q.trim() || isStreaming) return;

    setInputQuery('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }

    const userMsgId = 'user_' + Date.now();
    const assistantMsgId = 'asst_' + Date.now();

    const userMessage: ChatMessage = {
      id: userMsgId,
      role: 'user',
      content: q,
      timestamp: new Date().toISOString(),
    };

    const initialAssistantMessage: ChatMessage = {
      id: assistantMsgId,
      role: 'assistant',
      content: '',
      timestamp: new Date().toISOString(),
    };

    setMessages((prev) => [...prev, userMessage, initialAssistantMessage]);
    setIsStreaming(true);

    const controller = new AbortController();
    abortControllerRef.current = controller;

    try {
      const response = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: q, session_id: sessionId }),
        signal: controller.signal,
      });

      if (!response.ok) {
        throw new Error(`Server returned HTTP ${response.status}`);
      }

      const receivedSessionId = response.headers.get('X-Chat-Session-Id');
      if (receivedSessionId && receivedSessionId !== sessionId) {
        onSessionChange(receivedSessionId, q);
      }

      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      let accumulatedText = '';
      let extractedCitations: Citation[] = [];
      let traceId = '';

      if (reader) {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          const chunkStr = decoder.decode(value, { stream: true });
          const lines = chunkStr.split('\n\n');

          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try {
                const parsed = JSON.parse(line.substring(6));
                if (parsed.token) {
                  accumulatedText += parsed.token;
                  setMessages((prev) =>
                    prev.map((m) =>
                      m.id === assistantMsgId ? { ...m, content: accumulatedText } : m
                    )
                  );
                }
                if (parsed.done) {
                  extractedCitations = parsed.citations || [];
                  traceId = parsed.trace_id || '';
                  setMessages((prev) =>
                    prev.map((m) =>
                      m.id === assistantMsgId
                        ? {
                            ...m,
                            content: accumulatedText,
                            citations: extractedCitations,
                            langfuse_trace_id: traceId,
                          }
                        : m
                    )
                  );
                }
              } catch {
                // Ignore parse errors on partial chunks
              }
            }
          }
        }
      }
    } catch (e: any) {
      if (e.name === 'AbortError') {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMsgId
              ? { ...m, content: m.content + '\n\n*(Generation stopped by user)*' }
              : m
          )
        );
      } else {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMsgId
              ? {
                  ...m,
                  content: `⚠️ Unable to retrieve response: ${e.message}`,
                }
              : m
          )
        );
      }
    } finally {
      setIsStreaming(false);
      abortControllerRef.current = null;
    }
  }

  function handleStopGeneration() {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
  }

  async function handleFeedback(messageId: string, traceId?: string, score: number = 1) {
    if (!traceId) return;
    try {
      await fetch('/api/chat/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message_id: messageId, trace_id: traceId, feedback: score }),
      });
      setFeedbackSent((prev) => ({ ...prev, [messageId]: score }));
    } catch (e) {
      console.error('Failed to submit feedback to Langfuse', e);
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
    {
      title: 'Summarize Executive Briefing',
      subtitle: 'Q3 Enterprise AI & Cloud Milestones (.gslides)',
      query: 'Summarize the Executive Briefing Q3 slides including strategic milestones and multi-cloud architecture.',
    },
    {
      title: 'Analyze Cloud Budget & Costs',
      subtitle: '2026 infrastructure spending breakdown (.gsheet)',
      query: 'What are our projected cloud infrastructure costs for Google Cloud vs Microsoft Azure in 2026?',
    },
    {
      title: 'Review AI Security Policies',
      subtitle: 'Data Loss Prevention & model guidelines (.gdoc)',
      query: 'What are the enterprise DLP and model access policies defined for AI queries?',
    },
    {
      title: 'Kubernetes Hybrid Architecture',
      subtitle: 'GKE + AKS service mesh & pgvector specs (.pdf)',
      query: 'Explain the Kubernetes hybrid cloud architecture and RAG microservices topology.',
    },
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
          background: 'rgba(10, 13, 20, 0.75)',
          backdropFilter: 'blur(10px)',
          zIndex: 10,
        }}
      >
        {/* Model Selector dropdown pill */}
        <div style={{ position: 'relative' }}>
          <button
            onClick={() => setIsModelDropdownOpen(!isModelDropdownOpen)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.5rem',
              padding: '0.4rem 0.85rem',
              background: 'var(--app-surface)',
              border: '1px solid var(--app-border)',
              borderRadius: 'var(--radius-md)',
              color: 'var(--app-text)',
              fontSize: '0.85rem',
              fontWeight: 600,
              cursor: 'pointer',
              transition: 'all 0.15s ease',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.borderColor = 'var(--brand-primary)';
            }}
            onMouseLeave={(e) => {
              if (!isModelDropdownOpen) {
                e.currentTarget.style.borderColor = 'var(--app-border)';
              }
            }}
          >
            <div
              style={{
                width: '18px',
                height: '18px',
                borderRadius: '4px',
                background: 'var(--brand-gradient)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Bot size={11} color="#ffffff" />
            </div>
            <span>{selectedModel}</span>
            <ChevronDown size={14} color="var(--app-text-muted)" />
          </button>

          {isModelDropdownOpen && (
            <div
              style={{
                position: 'absolute',
                top: 'calc(100% + 6px)',
                left: 0,
                width: '280px',
                background: 'var(--app-surface)',
                border: '1px solid var(--app-border)',
                borderRadius: 'var(--radius-md)',
                boxShadow: '0 10px 30px rgba(0,0,0,0.6)',
                padding: '0.4rem',
                zIndex: 50,
              }}
            >
              {[
                { name: 'Enterprise RAG (GPT-4o Mini & Hybrid)', desc: 'Optimized speed & multi-cloud retrieval' },
                { name: 'Enterprise RAG High-Precision (GPT-4o)', desc: 'Deep synthesis & complex table analysis' },
              ].map((model) => (
                <div
                  key={model.name}
                  onClick={() => {
                    setSelectedModel(model.name);
                    setIsModelDropdownOpen(false);
                  }}
                  style={{
                    padding: '0.55rem 0.75rem',
                    borderRadius: 'var(--radius-sm)',
                    cursor: 'pointer',
                    background: selectedModel === model.name ? 'var(--brand-bg)' : 'transparent',
                    border: selectedModel === model.name ? '1px solid rgba(0, 120, 212, 0.4)' : '1px solid transparent',
                    marginBottom: '0.2rem',
                  }}
                  onMouseEnter={(e) => {
                    if (selectedModel !== model.name) e.currentTarget.style.background = 'var(--app-surface-hover)';
                  }}
                  onMouseLeave={(e) => {
                    if (selectedModel !== model.name) e.currentTarget.style.background = 'transparent';
                  }}
                >
                  <div style={{ fontSize: '0.825rem', fontWeight: 600, color: 'var(--app-text)' }}>
                    {model.name}
                  </div>
                  <div style={{ fontSize: '0.72rem', color: 'var(--app-text-muted)' }}>
                    {model.desc}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Right shortcut: Manage Knowledge Base Drawer */}
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
            e.currentTarget.style.color = '#38bdf8';
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
                Welcome, {userName}. Ask questions across your synced OneDrive and Google Drive files. Answers are verified and grounded in enterprise documents with clickable citations.
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
                          background: 'linear-gradient(135deg, rgba(0, 120, 212, 0.25) 0%, rgba(6, 182, 212, 0.15) 100%)',
                          border: '1px solid rgba(0, 120, 212, 0.35)',
                          borderRadius: '20px',
                          padding: '0.9rem 1.25rem',
                          color: '#ffffff',
                          fontSize: '0.95rem',
                          lineHeight: '1.6',
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
                            color: '#38bdf8',
                            marginBottom: '0.4rem',
                          }}
                        >
                          Enterprise Cloud RAG
                        </div>

                        {/* Content */}
                        <div className="prose">
                          {msg.content ? (
                            <ReactMarkdown>{msg.content}</ReactMarkdown>
                          ) : (
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--app-text-muted)' }}>
                              <Sparkles size={16} color="var(--brand-cyan)" className="animate-spin" />
                              <span>Searching multi-cloud documents & generating answer...</span>
                            </div>
                          )}
                        </div>

                        {/* Grounded Citations */}
                        {msg.citations && msg.citations.length > 0 && (
                          <div
                            style={{
                              marginTop: '1.25rem',
                              paddingTop: '0.85rem',
                              borderTop: '1px solid var(--app-border)',
                            }}
                          >
                            <span
                              style={{
                                fontSize: '0.72rem',
                                fontWeight: 700,
                                textTransform: 'uppercase',
                                letterSpacing: '0.05em',
                                color: 'var(--app-text-muted)',
                                display: 'block',
                                marginBottom: '0.5rem',
                              }}
                            >
                              Grounded Sources:
                            </span>

                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
                              {msg.citations.map((cite) => {
                                const isGDrive = cite.drive_type === 'google_drive';
                                return (
                                  <a
                                    key={cite.source_id}
                                    href={cite.onedrive_url || '#'}
                                    target="_blank"
                                    rel="noreferrer"
                                    style={{
                                      display: 'inline-flex',
                                      alignItems: 'center',
                                      gap: '0.4rem',
                                      padding: '0.35rem 0.65rem',
                                      background: isGDrive ? 'var(--gdrive-green-bg)' : 'var(--onedrive-blue-bg)',
                                      border: isGDrive ? '1px solid rgba(16, 185, 129, 0.3)' : '1px solid rgba(0, 120, 212, 0.35)',
                                      borderRadius: 'var(--radius-sm)',
                                      color: isGDrive ? '#6ee7b7' : '#93c5fd',
                                      fontSize: '0.75rem',
                                      fontWeight: 600,
                                      textDecoration: 'none',
                                      transition: 'all 0.15s ease',
                                    }}
                                  >
                                    <FileText size={12} />
                                    <span style={{ opacity: 0.85, fontSize: '0.675rem', textTransform: 'uppercase' }}>
                                      {isGDrive ? 'G-Drive' : 'OneDrive'}
                                    </span>
                                    [{cite.source_id}] {cite.filename}
                                    {cite.page && ` • P.${cite.page}`}
                                    {cite.section && ` • ${cite.section}`}
                                    <ExternalLink size={10} />
                                  </a>
                                );
                              })}
                            </div>
                          </div>
                        )}

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

                          {msg.langfuse_trace_id && (
                            <>
                              <button
                                onClick={() => handleFeedback(msg.id, msg.langfuse_trace_id, 1)}
                                style={{
                                  background: feedbackSent[msg.id] === 1 ? 'rgba(16,185,129,0.15)' : 'transparent',
                                  border: 'none',
                                  color: feedbackSent[msg.id] === 1 ? '#34d399' : 'var(--app-text-muted)',
                                  cursor: 'pointer',
                                  padding: '2px 4px',
                                  borderRadius: '4px',
                                }}
                                title="Good response (logged to Langfuse)"
                              >
                                <ThumbsUp size={13} />
                              </button>
                              <button
                                onClick={() => handleFeedback(msg.id, msg.langfuse_trace_id, -1)}
                                style={{
                                  background: feedbackSent[msg.id] === -1 ? 'rgba(244,63,94,0.15)' : 'transparent',
                                  border: 'none',
                                  color: feedbackSent[msg.id] === -1 ? '#fb7185' : 'var(--app-text-muted)',
                                  cursor: 'pointer',
                                  padding: '2px 4px',
                                  borderRadius: '4px',
                                }}
                                title="Poor response (logged to Langfuse)"
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
            boxShadow: '0 8px 32px rgba(0, 0, 0, 0.5)',
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
                background: 'rgba(255, 255, 255, 0.05)',
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
              <span>Drive Knowledge Attached</span>
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
          Enterprise Cloud RAG • Verified against synced OneDrive & Google Drive sources • OpenTelemetry & Langfuse monitored
        </span>
      </div>
    </div>
  );
}

export default ChatBox;
