import { useState, useRef, useEffect } from 'react';
import {
  Send,
  Bot,
  User as UserIcon,
  Sparkles,
  ExternalLink,
  ThumbsUp,
  ThumbsDown,
  RefreshCw,
  FileText,
  Check,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import { ChatMessage, Citation } from '../../types';

export function ChatBox() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputQuery, setInputQuery] = useState<string>('');
  const [isStreaming, setIsStreaming] = useState<boolean>(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [feedbackSent, setFeedbackSent] = useState<Record<string, number>>({});
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollToBottom();
  }, [messages, isStreaming]);

  function scrollToBottom() {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }

  async function handleSend(queryText?: string) {
    const q = queryText || inputQuery;
    if (!q.trim() || isStreaming) return;

    setInputQuery('');
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

    try {
      const response = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: q, session_id: sessionId }),
      });

      if (!response.ok) {
        throw new Error(`Server returned ${response.status}`);
      }

      const receivedSessionId = response.headers.get('X-Chat-Session-Id');
      if (receivedSessionId) setSessionId(receivedSessionId);

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
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantMsgId
            ? {
                ...m,
                content: `⚠️ Error retrieving answer from OneDrive RAG: ${e.message}`,
              }
            : m
        )
      );
    } finally {
      setIsStreaming(false);
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

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '640px' }} className="glass-panel">
      {/* Header */}
      <div
        style={{
          padding: '1rem 1.5rem',
          borderBottom: '1px solid var(--border-color)',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          background: 'rgba(15, 23, 42, 0.4)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
          <div
            style={{
              padding: '0.4rem',
              borderRadius: '8px',
              background: 'linear-gradient(135deg, #8B5CF6 0%, #06B6D4 100%)',
            }}
          >
            <Bot size={18} color="#ffffff" />
          </div>
          <div>
            <h3 style={{ fontSize: '1rem', fontWeight: 700 }}>Grounded OneDrive Assistant</h3>
            <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
              Answers strictly retrieved from OneDrive with verified citations
            </span>
          </div>
        </div>

        <button
          className="btn btn-secondary"
          onClick={() => {
            setMessages([]);
            setSessionId(null);
          }}
          style={{ padding: '0.4rem 0.8rem', fontSize: '0.75rem' }}
        >
          <RefreshCw size={12} /> New Session
        </button>
      </div>

      {/* Messages Scroll Area */}
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '1.5rem',
          display: 'flex',
          flexDirection: 'column',
          gap: '1.25rem',
        }}
      >
        {messages.length === 0 ? (
          <div
            style={{
              margin: 'auto',
              textAlign: 'center',
              maxWidth: '520px',
              color: 'var(--text-secondary)',
              padding: '2rem',
            }}
          >
            <Sparkles size={40} color="#06b6d4" style={{ margin: '0 auto 1rem' }} />
            <h4 style={{ fontSize: '1.2rem', fontWeight: 700, color: 'var(--text-primary)', marginBottom: '0.5rem' }}>
              Ask anything about your OneDrive files
            </h4>
            <p style={{ fontSize: '0.875rem', lineHeight: '1.6', marginBottom: '1.5rem' }}>
              The chatbot uses metadata-aware semantic retrieval to answer questions with citations linking back to original OneDrive documents.
            </p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', textAlign: 'left' }}>
              {[
                'What is the annual leave allowance?',
                'Show me all Finance documents modified this month',
                'What is our API authentication mechanism?',
                'Summarize the microservices architecture',
              ].map((sample, idx) => (
                <button
                  key={idx}
                  onClick={() => handleSend(sample)}
                  style={{
                    padding: '0.65rem 1rem',
                    borderRadius: 'var(--radius-md)',
                    background: 'rgba(255, 255, 255, 0.03)',
                    border: '1px solid var(--border-color)',
                    color: 'var(--text-primary)',
                    fontSize: '0.8125rem',
                    textAlign: 'left',
                    cursor: 'pointer',
                    transition: 'all 0.15s ease',
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'rgba(0,120,212,0.5)')}
                  onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--border-color)')}
                >
                  💬 {sample}
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((msg) => (
            <div
              key={msg.id}
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: msg.role === 'user' ? 'flex-end' : 'flex-start',
              }}
            >
              <div
                style={{
                  display: 'flex',
                  gap: '0.75rem',
                  maxWidth: '85%',
                  alignItems: 'flex-start',
                  flexDirection: msg.role === 'user' ? 'row-reverse' : 'row',
                }}
              >
                <div
                  style={{
                    width: '32px',
                    height: '32px',
                    borderRadius: '8px',
                    background: msg.role === 'user' ? 'rgba(0,120,212,0.2)' : 'rgba(139,92,246,0.2)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    flexShrink: 0,
                  }}
                >
                  {msg.role === 'user' ? (
                    <UserIcon size={16} color="#60a5fa" />
                  ) : (
                    <Bot size={16} color="#a78bfa" />
                  )}
                </div>

                <div
                  style={{
                    background:
                      msg.role === 'user'
                        ? 'linear-gradient(135deg, rgba(0,120,212,0.4) 0%, rgba(0,90,158,0.5) 100%)'
                        : 'rgba(22, 28, 45, 0.85)',
                    border: '1px solid var(--border-color)',
                    borderRadius: 'var(--radius-lg)',
                    padding: '0.9rem 1.2rem',
                    fontSize: '0.925rem',
                    lineHeight: '1.6',
                    boxShadow: '0 4px 16px rgba(0,0,0,0.2)',
                  }}
                >
                  <ReactMarkdown>{msg.content || '...'}</ReactMarkdown>

                  {/* Interactive Source Citations */}
                  {msg.citations && msg.citations.length > 0 && (
                    <div
                      style={{
                        marginTop: '1rem',
                        paddingTop: '0.75rem',
                        borderTop: '1px solid var(--border-color)',
                      }}
                    >
                      <span
                        style={{
                          fontSize: '0.75rem',
                          fontWeight: 700,
                          textTransform: 'uppercase',
                          letterSpacing: '0.05em',
                          color: 'var(--text-secondary)',
                          display: 'block',
                          marginBottom: '0.5rem',
                        }}
                      >
                        Verified Sources (OneDrive & Google Drive):
                      </span>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
                        {msg.citations.map((cite) => {
                          const isGDrive = cite.drive_type === 'google_drive';
                          return (
                            <a
                              key={cite.source_id}
                              href={cite.onedrive_url}
                              target="_blank"
                              rel="noreferrer"
                              style={{
                                display: 'inline-flex',
                                alignItems: 'center',
                                gap: '0.4rem',
                                padding: '0.35rem 0.65rem',
                                background: isGDrive ? 'rgba(16, 185, 129, 0.12)' : 'rgba(0, 120, 212, 0.12)',
                                border: isGDrive ? '1px solid rgba(16, 185, 129, 0.3)' : '1px solid rgba(0, 120, 212, 0.3)',
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

                  {/* Feedback Controls (Langfuse Evaluator) */}
                  {msg.role === 'assistant' && msg.langfuse_trace_id && (
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        marginTop: '0.75rem',
                        fontSize: '0.75rem',
                        color: 'var(--text-muted)',
                      }}
                    >
                      <span style={{ fontFamily: 'var(--font-mono)' }}>
                        trace: {msg.langfuse_trace_id.substring(0, 8)}...
                      </span>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                        <span>Helpful?</span>
                        <button
                          onClick={() => handleFeedback(msg.id, msg.langfuse_trace_id, 1)}
                          style={{
                            background: feedbackSent[msg.id] === 1 ? 'rgba(16,185,129,0.2)' : 'transparent',
                            border: 'none',
                            color: feedbackSent[msg.id] === 1 ? '#34d399' : 'var(--text-secondary)',
                            cursor: 'pointer',
                            padding: '2px 4px',
                            borderRadius: '4px',
                          }}
                          title="Thumbs Up (logged to Langfuse)"
                        >
                          <ThumbsUp size={12} />
                        </button>
                        <button
                          onClick={() => handleFeedback(msg.id, msg.langfuse_trace_id, -1)}
                          style={{
                            background: feedbackSent[msg.id] === -1 ? 'rgba(244,63,94,0.2)' : 'transparent',
                            border: 'none',
                            color: feedbackSent[msg.id] === -1 ? '#fb7185' : 'var(--text-secondary)',
                            cursor: 'pointer',
                            padding: '2px 4px',
                            borderRadius: '4px',
                          }}
                          title="Thumbs Down (logged to Langfuse)"
                        >
                          <ThumbsDown size={12} />
                        </button>
                        {feedbackSent[msg.id] && (
                          <span style={{ color: '#34d399', display: 'flex', alignItems: 'center', gap: '2px' }}>
                            <Check size={10} /> Logged
                          </span>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>
          ))
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input Form */}
      <div
        style={{
          padding: '1rem 1.5rem',
          borderTop: '1px solid var(--border-color)',
          background: 'rgba(15, 23, 42, 0.4)',
        }}
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            handleSend();
          }}
          style={{ display: 'flex', gap: '0.75rem' }}
        >
          <input
            type="text"
            placeholder="Ask a question about your OneDrive documents..."
            value={inputQuery}
            onChange={(e) => setInputQuery(e.target.value)}
            disabled={isStreaming}
            style={{
              flex: 1,
              padding: '0.75rem 1rem',
              borderRadius: 'var(--radius-md)',
              background: 'rgba(15, 23, 42, 0.8)',
              border: '1px solid var(--border-color)',
              color: 'var(--text-primary)',
              fontSize: '0.875rem',
              outline: 'none',
            }}
          />
          <button type="submit" className="btn btn-primary" disabled={!inputQuery.trim() || isStreaming}>
            {isStreaming ? <RefreshCw size={16} className="animate-spin" /> : <Send size={16} />}
            <span>Ask</span>
          </button>
        </form>
      </div>
    </div>
  );
}
