import { useEffect, useRef, useState } from 'react';
import { api } from '../../api/client';
import { renderMarkdown } from '../../utils/markdown';
import { useTicketStore } from '../../store/ticketStore';
import { useUIStore } from '../../store/uiStore';
import { pushUrl } from '../../utils/url';
import type { ChatMessage, ChatStreamEvent, ChatSource, TicketMetrics, MessageFeedback } from '../../types';

interface EngagementStatus {
  status: string;
  engagement_type?: string;
  human_took_over?: boolean;
  resolved_by_ai?: boolean;
  kb_articles_referenced?: string[];
  suggested_category_id?: number;
  suggested_category_name?: string;
  category_confidence?: number;
  similar_tickets?: Array<{ id: number; ticket_number: string; subject: string; status: string; priority: string }>;
}

interface AtlasTabProps {
  ticketId: number;
  ticketSubject: string;
  ticketDescription: string;
  isDevItem?: boolean;
}

/** Lightweight Atlas chat embedded in the ticket detail panel. */
export function AtlasTab({ ticketId, ticketSubject, ticketDescription, isDevItem }: AtlasTabProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [metrics, setMetrics] = useState<TicketMetrics | null>(null);
  const [engagement, setEngagement] = useState<EngagementStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sentMessages, setSentMessages] = useState<Map<number, 'reply' | 'note'>>(new Map());
  const [sendingIndex, setSendingIndex] = useState<number | null>(null);
  const [feedback, setFeedback] = useState<Map<number, MessageFeedback>>(new Map());
  const [similarTickets, setSimilarTickets] = useState<Array<{ id: number; ticket_number: string; subject: string; status: string; priority: string }>>([]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Watch active ticket for status/assignee changes to refresh engagement dot
  const activeTicket = useTicketStore((s) => s.activeTicket);
  const ticketStatus = activeTicket?.id === ticketId ? activeTicket.status : undefined;
  const ticketAssignee = activeTicket?.id === ticketId ? activeTicket.assignee_id : undefined;

  // Load existing Atlas conversation + engagement status for this ticket
  useEffect(() => {
    api.getConversationByTicket(ticketId).then((conv) => {
      if (conv && conv.id) {
        setConversationId(conv.id);
        setMessages(Array.isArray(conv.messages) ? conv.messages : []);
        if (conv.feedback && Array.isArray(conv.feedback)) {
          const fbMap = new Map<number, MessageFeedback>();
          for (const fb of conv.feedback) {
            fbMap.set(fb.message_index, fb.rating as MessageFeedback);
          }
          setFeedback(fbMap);
        }
      }
    }).catch((err) => { console.warn('AtlasTab: failed to load conversation', err); });

    // Load metrics
    api.getTicketMetrics(ticketId).then((m) => {
      if (m && m.ticket_id) setMetrics(m);
    }).catch(() => {});

    // Load similar tickets (standalone, works regardless of auto-engage)
    api.getSimilarTickets(ticketId).then((similar) => {
      if (Array.isArray(similar)) setSimilarTickets(similar);
    }).catch(() => {});
  }, [ticketId]);

  // Re-fetch engagement status when ticket ID, status, or assignee changes
  useEffect(() => {
    const fetchEngagement = () =>
      api.getEngagementStatus(ticketId).then((e) => {
        if (e && e.status !== 'none') setEngagement(e);
        else setEngagement(null);
      }).catch(() => {});

    fetchEngagement();

    // Background audit thread updates engagement asynchronously on resolve/close —
    // re-fetch after a short delay to catch the updated status
    if (ticketStatus === 'resolved' || ticketStatus === 'closed_not_resolved') {
      const timer = setTimeout(fetchEngagement, 3000);
      return () => clearTimeout(timer);
    }
  }, [ticketId, ticketStatus, ticketAssignee]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, statusMessage]);

  const sendMessage = (query: string) => {
    if (!query.trim() || streaming) return;

    setMessages((prev) => [...prev, { role: 'user', content: query }, { role: 'assistant', content: '' }]);
    setStreaming(true);
    setStatusMessage(null);
    setError(null);
    setInput('');

    const controller = api.chatStream(
      { query, conversation_id: conversationId ?? undefined, ticket_id: ticketId },
      (event: ChatStreamEvent) => {
        switch (event.type) {
          case 'conversation_id':
            setConversationId(event.conversation_id ?? null);
            break;
          case 'status':
            setStatusMessage(event.content || null);
            break;
          case 'text':
            setStatusMessage(null);
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last?.role === 'assistant') {
                updated[updated.length - 1] = { ...last, content: last.content + (event.content || '') };
              }
              return updated;
            });
            break;
          case 'sources':
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last?.role === 'assistant') {
                updated[updated.length - 1] = { ...last, sources: (event.sources || []) as ChatSource[] };
              }
              return updated;
            });
            break;
          case 'escalation':
            setMessages((prev) => [...prev, { role: 'assistant', content: '' }]);
            setStatusMessage(event.content || 'Searching deeper...');
            break;
          case 'resolved':
            // Atlas auto-resolved the ticket — refresh so status reflects immediately
            useTicketStore.getState().loadTicket(ticketId);
            useTicketStore.getState().loadTickets();
            break;
          case 'done':
            setStreaming(false);
            setStatusMessage(null);
            abortRef.current = null;
            break;
        }
      },
      (err: Error) => {
        setError(err.message);
        setStreaming(false);
        setStatusMessage(null);
        abortRef.current = null;
      },
    );
    abortRef.current = controller;
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    sendMessage(input);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input);
    }
  };

  const handleSendToTicket = async (index: number, content: string, isInternal: boolean) => {
    setSendingIndex(index);
    try {
      await api.sendChatResponseToTicket(content, ticketId, isInternal);
      setSentMessages((prev) => new Map(prev).set(index, isInternal ? 'note' : 'reply'));
      useTicketStore.getState().loadTicket(ticketId);
    } catch {
      setError('Failed to send to ticket');
    }
    setSendingIndex(null);
  };

  const handleFeedback = async (messageIndex: number, rating: 'positive' | 'negative') => {
    if (!conversationId) return;
    const prev = feedback.get(messageIndex);
    setFeedback((f) => new Map(f).set(messageIndex, rating));
    try {
      await api.submitFeedback(conversationId, messageIndex, rating);
    } catch {
      setFeedback((f) => {
        const m = new Map(f);
        if (prev) m.set(messageIndex, prev);
        else m.delete(messageIndex);
        return m;
      });
    }
  };

  const isCompleteAssistant = (m: ChatMessage, i: number) =>
    m.role === 'assistant' && m.content && !(streaming && i === messages.length - 1);

  // Engagement status dot color
  const statusColor = engagement
    ? engagement.status === 'active' ? 'var(--t-success)' : engagement.status === 'passive' ? '#ffb400' : 'var(--t-error)'
    : 'var(--t-text-dim)';
  const statusLabel = engagement
    ? engagement.status === 'active' ? 'Active' : engagement.status === 'passive' ? 'Passive' : 'Closed'
    : 'No engagement';

  // BYOK gate: in demo mode without configured API keys, block the Atlas chat UI
  const atlasConfig = window.__APP_CONFIG__;
  if (atlasConfig?.demo_mode === true && atlasConfig?.byok_configured !== true) {
    const navigateToBilling = () => {
      useUIStore.getState().setView('admin');
      pushUrl('/admin/billing');
    };
    return (
      <div
        className="atlas-tab"
        style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}
      >
        <div
          style={{
            textAlign: 'center',
            color: 'var(--t-text-muted)',
            fontSize: '13px',
            lineHeight: '1.6',
            maxWidth: '300px',
          }}
        >
          AI features require API keys.{' '}
          <span
            role="link"
            tabIndex={0}
            aria-label="Navigate to Settings, Billing"
            onClick={navigateToBilling}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); navigateToBilling(); } }}
            style={{ color: '#06b6d4', textDecoration: 'underline', cursor: 'pointer' }}
          >
            Settings &rarr; Billing
          </span>
          {' '}to get started.
        </div>
      </div>
    );
  }

  return (
    <div className="atlas-tab">
      {/* Ticket context header */}
      <div className="atlas-context-header">
        <span className="atlas-context-icon">⬡</span>
        <span className="atlas-context-subject">{ticketSubject}</span>
        {!isDevItem && (
          <>
            <span className="atlas-engagement-dot" style={{ background: statusColor }} title={`Atlas: ${statusLabel}`} />
            <span className="atlas-engagement-label">{statusLabel}</span>
          </>
        )}
        {isDevItem && (
          <span style={{ fontSize: 10, color: 'var(--t-text-dim)', marginLeft: 'auto' }}>Dev Mode</span>
        )}
      </div>

      {/* Similar tickets alert — fetched on demand, not dependent on auto-engage */}
      {!isDevItem && similarTickets.length > 0 && (
        <div className="atlas-similar-bar">
          <span className="atlas-similar-icon">⚠</span>
          <span className="atlas-similar-text">
            {similarTickets.length} similar open ticket{similarTickets.length > 1 ? 's' : ''} found:
          </span>
          <div className="atlas-similar-list">
            {similarTickets.map((t) => (
              <button
                key={t.id}
                className="atlas-similar-chip"
                onClick={() => useUIStore.getState().openTicketDetail(t.id)}
                title={t.subject}
              >
                {t.ticket_number || `#${t.id}`}: {t.subject.length > 40 ? t.subject.slice(0, 40) + '...' : t.subject}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Category suggestion (if auto-engage suggested one) — support only */}
      {!isDevItem && engagement?.suggested_category_name && (
        <div className="atlas-category-suggestion">
          <span className="atlas-metric-label">Suggested Category</span>
          <span className="atlas-metric-value">{engagement.suggested_category_name}</span>
          {engagement.category_confidence != null && (
            <span className="atlas-metric-conf">{Math.round(engagement.category_confidence * 100)}%</span>
          )}
        </div>
      )}

      {/* Metrics bar (if available) — support only */}
      {!isDevItem && metrics && metrics.suggested_assignee_name && (
        <div className="atlas-metrics-bar">
          <span className="atlas-metric">
            <span className="atlas-metric-label">Suggested Agent</span>
            <span className="atlas-metric-value">{metrics.suggested_assignee_name}</span>
            {metrics.routing_confidence != null && (
              <span className="atlas-metric-conf">{Math.round(metrics.routing_confidence * 100)}%</span>
            )}
          </span>
          {metrics.routing_reason && (
            <span className="atlas-metric-reason">{metrics.routing_reason}</span>
          )}
        </div>
      )}

      {/* Messages */}
      <div className="atlas-messages" ref={scrollRef}>
        {messages.length === 0 && (
          <div className="atlas-empty">
            <div className="atlas-empty-icon">⬡</div>
            <div className="atlas-empty-text">
              Atlas has context on <strong>{ticketSubject}</strong>.
            </div>
            <div className="atlas-empty-hint">Ask a question or type &quot;analyze&quot; for a fresh analysis.</div>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`atlas-msg atlas-msg-${m.role}`}>
            {m.role === 'assistant' && <span className="atlas-msg-label">Atlas</span>}
            {m.role === 'assistant' ? (
              <div
                className="atlas-msg-content chat-markdown"
                dangerouslySetInnerHTML={{ __html: renderMarkdown(m.content || '') }}
              />
            ) : (
              <div className="atlas-msg-content">{m.content}</div>
            )}
            {streaming && i === messages.length - 1 && !m.content && (
              <span className="atlas-cursor" />
            )}
            {!isDevItem && m.sources && m.sources.length > 0 && (
              <div className="atlas-sources">
                {m.sources.map((s, j) => (
                  <span key={j} className="atlas-source-chip">
                    <span className="atlas-source-module">{s.module}</span>
                    <span className="atlas-source-title">{s.title}</span>
                  </span>
                ))}
              </div>
            )}
            {/* Action bar: feedback + send-to-ticket */}
            {isCompleteAssistant(m, i) && (
              <div className="atlas-msg-actions">
                <div className="chat-feedback">
                  <button
                    className={`chat-feedback-btn ${feedback.get(i) === 'positive' ? 'active positive' : ''}`}
                    onClick={() => handleFeedback(i, 'positive')}
                    title="Helpful"
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M7 10v12" /><path d="M15 5.88L14 10h5.83a2 2 0 011.92 2.56l-2.33 8A2 2 0 0117.5 22H4a2 2 0 01-2-2v-8a2 2 0 012-2h2.76a2 2 0 001.79-1.11L12 2a3.13 3.13 0 013 3.88z" />
                    </svg>
                  </button>
                  <button
                    className={`chat-feedback-btn ${feedback.get(i) === 'negative' ? 'active negative' : ''}`}
                    onClick={() => handleFeedback(i, 'negative')}
                    title="Not helpful"
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M17 14V2" /><path d="M9 18.12L10 14H4.17a2 2 0 01-1.92-2.56l2.33-8A2 2 0 017.5 2H20a2 2 0 012 2v8a2 2 0 01-2 2h-2.76a2 2 0 00-1.79 1.11L12 22a3.13 3.13 0 01-3-3.88z" />
                    </svg>
                  </button>
                </div>
                <div className="atlas-send-actions">
                  {sentMessages.has(i) ? (
                    <span className="atlas-sent-badge">
                      Sent as {sentMessages.get(i) === 'reply' ? 'Reply' : 'Note'} ✓
                    </span>
                  ) : (
                    <>
                      <button
                        className="atlas-action-btn atlas-action-reply"
                        onClick={() => handleSendToTicket(i, m.content, false)}
                        disabled={sendingIndex === i}
                        title="Send as a reply visible to the requester"
                      >
                        {sendingIndex === i ? '...' : 'Send as Reply'}
                      </button>
                      <button
                        className="atlas-action-btn atlas-action-note"
                        onClick={() => handleSendToTicket(i, m.content, true)}
                        disabled={sendingIndex === i}
                        title="Send as an internal note (agents only)"
                      >
                        {sendingIndex === i ? '...' : 'Send as Note'}
                      </button>
                    </>
                  )}
                </div>
              </div>
            )}
          </div>
        ))}
        {statusMessage && (
          <div className="atlas-status">{statusMessage}</div>
        )}
        {error && <div className="atlas-error">{error}</div>}
      </div>

      {/* Input */}
      <form onSubmit={handleSubmit} className="atlas-input-bar">
        <textarea
          className="atlas-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask Atlas about this ticket..."
          rows={1}
          disabled={streaming}
        />
        <button
          type={streaming ? 'button' : 'submit'}
          className={`atlas-send-btn ${streaming ? 'streaming' : ''}`}
          disabled={!streaming && !input.trim()}
          onClick={streaming ? () => { abortRef.current?.abort(); setStreaming(false); } : undefined}
        >
          {streaming ? '■' : '→'}
        </button>
      </form>
    </div>
  );
}
