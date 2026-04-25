import { useState, useRef, useEffect, useCallback } from 'react';
import { api } from '../../api/client';
import { renderMarkdown } from '../../utils/markdown';
import type { ChatMessage, ChatStreamEvent, ChatSource, AIConversation, ArticleRecommendation } from '../../types';
import { ReplyToolbar } from '../common/ReplyToolbar';

type WidgetView = 'history' | 'chat';

function timeAgo(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 2) return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export function ChatWidget() {
  const [isOpen, setIsOpen] = useState(false);
  const [view, setView] = useState<WidgetView>('history');
  const [conversations, setConversations] = useState<AIConversation[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [statusFilter, setStatusFilter] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [statusMessage, setStatusMessage] = useState('');
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [hasUnread, setHasUnread] = useState(false);
  const [linkedTicket, setLinkedTicket] = useState<{ id: number; number: string } | null>(null);
  const [caseCreating, setCaseCreating] = useState(false);
  const [ticketAutoResolved, setTicketAutoResolved] = useState(false);
  const [articleRecs, setArticleRecs] = useState<ArticleRecommendation[]>([]);
  const [articleRatings, setArticleRatings] = useState<Map<number, boolean | null>>(new Map());
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const exchangeCountRef = useRef(0);
  // Refs to avoid stale closures in streaming callbacks
  const conversationIdRef = useRef<number | null>(null);
  const linkedTicketRef = useRef<{ id: number; number: string } | null>(null);
  const caseCreatingRef = useRef(false);
  const messagesRef = useRef<ChatMessage[]>([]);

  // Auto-scroll
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, statusMessage]);

  // Auto-grow textarea
  useEffect(() => {
    const ta = textareaRef.current;
    if (ta) {
      ta.style.height = 'auto';
      ta.style.height = Math.min(ta.scrollHeight, 140) + 'px';
    }
  }, [input]);

  // On open: load history, decide which view to show
  useEffect(() => {
    if (!isOpen) return;
    setHasUnread(false);

    // If we already have an active conversation in progress, go straight to chat
    if (messages.length > 0) {
      setView('chat');
      setTimeout(() => textareaRef.current?.focus(), 200);
      return;
    }

    // Load previous conversations
    setLoadingHistory(true);
    api.listConversations().then(convs => {
      setConversations(convs);
      // If no history, go straight to new chat
      if (!convs || convs.length === 0) {
        setView('chat');
        setTimeout(() => textareaRef.current?.focus(), 200);
      } else {
        setView('history');
      }
    }).catch(() => {
      setView('chat');
      setTimeout(() => textareaRef.current?.focus(), 200);
    }).finally(() => setLoadingHistory(false));
  }, [isOpen]);

  // Focus input when entering chat view
  useEffect(() => {
    if (view === 'chat') {
      setTimeout(() => textareaRef.current?.focus(), 150);
    }
  }, [view]);

  // Keep refs in sync with state (avoids stale closures in streaming callbacks)
  useEffect(() => { conversationIdRef.current = conversationId; }, [conversationId]);
  useEffect(() => { linkedTicketRef.current = linkedTicket; }, [linkedTicket]);
  useEffect(() => { messagesRef.current = messages; }, [messages]);

  const startNewChat = () => {
    setMessages([]);
    setConversationId(null);
    conversationIdRef.current = null;
    setLinkedTicket(null);
    linkedTicketRef.current = null;
    caseCreatingRef.current = false;
    exchangeCountRef.current = 0;
    setInput('');
    setView('chat');
  };

  const resumeConversation = async (conv: AIConversation) => {
    try {
      const full = await api.getConversation(conv.id);
      const msgs: ChatMessage[] = Array.isArray(full.messages) ? full.messages : [];
      setMessages(msgs);
      setConversationId(conv.id);
      conversationIdRef.current = conv.id;
      exchangeCountRef.current = Math.floor(msgs.filter(m => m.role === 'user').length);
      setLinkedTicket(null);
      linkedTicketRef.current = null;
      caseCreatingRef.current = false;
      setView('chat');
    } catch {
      setView('chat');
    }
  };

  // Create case from chat — uses refs to avoid stale closures
  const createCaseFromChat = useCallback(async (convId?: number | null) => {
    const cid = convId ?? conversationIdRef.current;
    if (!cid || linkedTicketRef.current || caseCreatingRef.current) return;
    caseCreatingRef.current = true;
    setCaseCreating(true);
    try {
      const msgs = messagesRef.current;
      const firstUserMsg = msgs.find(m => m.role === 'user');
      const subject = firstUserMsg?.content.slice(0, 100) || 'Chat conversation';
      const transcript = msgs.map(m => `**${m.role === 'user' ? 'Customer' : 'Atlas'}:** ${m.content}`).join('\n\n');
      const result = await api.chatToCase({ conversation_id: cid, subject, transcript });
      const ticket = { id: result.ticket_id, number: result.ticket_number };
      setLinkedTicket(ticket);
      linkedTicketRef.current = ticket;
    } catch (e) {
      console.warn('Chat-to-case failed:', e);
    }
    caseCreatingRef.current = false;
    setCaseCreating(false);
  }, []);

  const handleSend = useCallback(() => {
    const q = input.trim();
    if (!q || loading) return;

    const userMsg: ChatMessage = { role: 'user', content: q };
    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    setLoading(true);
    setStreaming(true);
    setStatusMessage('');

    let assistantContent = '';
    let sources: ChatSource[] = [];
    let streamConvId: number | null = conversationIdRef.current;

    const controller = api.chatStream(
      { query: q, conversation_id: conversationIdRef.current || undefined, language: 'en', ticket_id: linkedTicketRef.current?.id },
      (event: ChatStreamEvent) => {
        switch (event.type) {
          case 'status':
            setStatusMessage(event.content || '');
            break;
          case 'text':
            assistantContent += event.content || '';
            setMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last?.role === 'assistant') {
                next[next.length - 1] = { ...last, content: assistantContent };
              } else {
                next.push({ role: 'assistant', content: assistantContent });
              }
              return next;
            });
            break;
          case 'sources':
            sources = event.sources || [];
            setMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last?.role === 'assistant') {
                next[next.length - 1] = { ...last, sources };
              }
              return next;
            });
            break;
          case 'conversation_id':
            if (event.conversation_id) {
              streamConvId = event.conversation_id;
              setConversationId(event.conversation_id);
              conversationIdRef.current = event.conversation_id;
            }
            break;
          case 'resolved':
            setTicketAutoResolved(true);
            // Fetch KB articles to show rating panel
            if (streamConvId) {
              api.getConversationArticles(streamConvId).then((recs) => {
                setArticleRecs(recs || []);
              }).catch(() => {});
            }
            break;
          case 'done':
            setStatusMessage('');
            setLoading(false);
            setStreaming(false);
            if (!isOpen) setHasUnread(true);
            exchangeCountRef.current += 1;

            // Auto-create case after first exchange (use refs, not state)
            if (exchangeCountRef.current === 1 && streamConvId) {
              setTimeout(() => createCaseFromChat(streamConvId), 200);
            }

            // Append to linked ticket if it exists (use ref)
            const lt = linkedTicketRef.current;
            if (lt) {
              api.chatToCaseAppend(lt.id, q, 'user').catch(() => {});
              if (assistantContent) {
                api.chatToCaseAppend(lt.id, assistantContent, 'assistant').catch(() => {});
              }
            }
            break;
        }
      },
      (_err) => {
        setStatusMessage('');
        setLoading(false);
        setStreaming(false);
        setMessages((prev) => [
          ...prev,
          { role: 'assistant', content: 'Sorry, something went wrong. Please try again.' },
        ]);
      },
    );
    controllerRef.current = controller;
  }, [input, loading, isOpen, createCaseFromChat]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleStop = () => {
    controllerRef.current?.abort();
    setLoading(false);
    setStreaming(false);
    setStatusMessage('');
  };

  const handleManualCreateCase = () => {
    createCaseFromChat();
  };

  const handleRateArticle = (rec: ArticleRecommendation, helpful: boolean) => {
    // Toggle off if already rated the same way
    const current = articleRatings.get(rec.id);
    const newVal = current === helpful ? null : helpful;
    setArticleRatings((prev) => new Map(prev).set(rec.id, newVal));
    api.rateArticle(rec.id, newVal).catch(() => {});
  };

  // Navigate to portal detail for the linked ticket
  const viewCase = () => {
    if (!linkedTicket) return;
    const base = window.location.pathname.replace(/\?.*/, '');
    window.location.href = `${base}?view=detail&id=${linkedTicket.id}`;
  };

  if (!isOpen) {
    return (
      <button
        className="chat-widget-bubble"
        onClick={() => setIsOpen(true)}
        title="Chat with Atlas"
      >
        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z" />
        </svg>
        {hasUnread && <span className="chat-widget-unread" />}
      </button>
    );
  }

  return (
    <div className="chat-widget-panel">
      <div className="chat-widget-header">
        <div className="chat-widget-header-left">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z" />
          </svg>
          <span className="chat-widget-title">Atlas</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          {view === 'chat' && (
            <>
              {linkedTicket ? (
                <span className="chat-widget-case-badge" onClick={viewCase} title="View case">
                  {linkedTicket.number}
                </span>
              ) : conversationId && messages.length >= 2 ? (
                <button className="chat-widget-case-badge" onClick={handleManualCreateCase} disabled={caseCreating} title="Create a support case from this chat">
                  {caseCreating ? '...' : 'Create Case'}
                </button>
              ) : null}
              {conversations.length > 0 && (
                <button className="chat-widget-history-btn" onClick={() => setView('history')} title="Previous chats">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="1 4 1 10 7 10" /><path d="M3.51 15a9 9 0 1 0 .49-4.5" />
                  </svg>
                </button>
              )}
            </>
          )}
          <button className="chat-widget-close" onClick={() => setIsOpen(false)} title="Minimize">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
          </button>
        </div>
      </div>

      {/* History view */}
      {view === 'history' && (
        <div className="chat-widget-history">
          {loadingHistory ? (
            <div className="chat-widget-history-loading">Loading...</div>
          ) : (
            <>
              <button className="chat-widget-new-chat" onClick={startNewChat}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
                </svg>
                New Chat
              </button>
              {conversations.length > 0 && (
                <div className="chat-widget-history-list">
                  <div className="chat-widget-history-label">Recent conversations</div>
                  {conversations.some(c => c.ticket_status) && (
                    <div className="chat-widget-status-filters">
                      <button className={`chat-widget-filter-btn ${!statusFilter ? 'active' : ''}`} onClick={() => setStatusFilter(null)}>All</button>
                      <button className={`chat-widget-filter-btn ${statusFilter === 'open' ? 'active' : ''}`} onClick={() => setStatusFilter(statusFilter === 'open' ? null : 'open')}>Open</button>
                      <button className={`chat-widget-filter-btn ${statusFilter === 'resolved' ? 'active' : ''}`} onClick={() => setStatusFilter(statusFilter === 'resolved' ? null : 'resolved')}>Resolved</button>
                      <button className={`chat-widget-filter-btn ${statusFilter === 'no_case' ? 'active' : ''}`} onClick={() => setStatusFilter(statusFilter === 'no_case' ? null : 'no_case')}>No Case</button>
                    </div>
                  )}
                  {conversations
                    .filter(c => {
                      if (!statusFilter) return true;
                      if (statusFilter === 'no_case') return !c.ticket_id;
                      return c.ticket_status === statusFilter;
                    })
                    .slice(0, 10).map(conv => (
                    <button key={conv.id} className="chat-widget-history-item" onClick={() => resumeConversation(conv)}>
                      <span className="chat-widget-history-msg">
                        {conv.first_message || 'Conversation'}
                      </span>
                      <span className="chat-widget-history-meta">
                        {conv.ticket_status && (
                          <span className={`chat-widget-case-badge badge-${conv.ticket_status}`}>
                            {conv.ticket_number || 'Case'}
                          </span>
                        )}
                        <span className="chat-widget-history-time">
                          {timeAgo(conv.updated_at || conv.created_at)}
                        </span>
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* Chat view */}
      {view === 'chat' && (
        <>
          <div className="chat-widget-messages">
            {messages.length === 0 && (
              <div className="chat-widget-empty">
                <div className="chat-widget-empty-icon">
                  <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z" />
                  </svg>
                </div>
                <div className="chat-widget-empty-title">Hey, I'm Atlas</div>
                <div className="chat-widget-empty-text">Ask me anything — I'm here to help.</div>
              </div>
            )}
            {messages.map((msg, i) => (
              <div key={i} className={`chat-widget-msg chat-widget-msg-${msg.role}${streaming && i === messages.length - 1 && msg.role === 'assistant' ? ' streaming' : ''}`}>
                <div
                  className="chat-widget-msg-content"
                  dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.content) }}
                />
                {streaming && i === messages.length - 1 && msg.role === 'assistant' && (
                  <span className="chat-widget-streaming-cursor" />
                )}
                {msg.sources && msg.sources.length > 0 && (
                  <div className="chat-widget-sources">
                    {msg.sources.map((src, j) => (
                      <a key={j} href={src.url || '#'} target="_blank" rel="noopener noreferrer" className="chat-widget-source">
                        {src.title}
                      </a>
                    ))}
                  </div>
                )}
              </div>
            ))}
            {statusMessage && (
              <div className="chat-widget-status">
                <span className="atlas-typing-dots"><span /><span /><span /></span>
                <span className="chat-widget-status-text">{statusMessage}</span>
              </div>
            )}
            {ticketAutoResolved && (
              <div className="chat-widget-resolved-panel">
                <div className="chat-widget-resolved-banner">
                  ✓ Your case has been marked as resolved
                </div>
                {articleRecs.length > 0 && (
                  <div className="chat-widget-article-feedback">
                    <p className="chat-widget-article-feedback-prompt">
                      To help us keep our support at its best, which article helped you most?
                    </p>
                    <table className="chat-widget-article-table">
                      <thead>
                        <tr>
                          <th>Article</th>
                          <th>👍</th>
                          <th>👎</th>
                        </tr>
                      </thead>
                      <tbody>
                        {articleRecs.map((rec) => {
                          const rating = articleRatings.has(rec.id) ? articleRatings.get(rec.id) : rec.user_helpful;
                          return (
                            <tr key={rec.id}>
                              <td>
                                {rec.url
                                  ? <a href={rec.url} target="_blank" rel="noopener noreferrer">{rec.title}</a>
                                  : rec.title}
                              </td>
                              <td>
                                <button
                                  className={`chat-widget-rate-btn${rating === true ? ' active-up' : ''}`}
                                  onClick={() => handleRateArticle(rec, true)}
                                  title="This helped"
                                >👍</button>
                              </td>
                              <td>
                                <button
                                  className={`chat-widget-rate-btn${rating === false ? ' active-down' : ''}`}
                                  onClick={() => handleRateArticle(rec, false)}
                                  title="This didn't help"
                                >👎</button>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          <div className="chat-widget-input">
            <div className="chat-widget-textarea-wrap">
              <ReplyToolbar textareaRef={textareaRef} setText={setInput} getCurrentText={() => input} />
              <textarea
                ref={textareaRef}
                className="chat-widget-textarea has-toolbar"
                placeholder="Type a message..."
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                disabled={loading}
                rows={1}
              />
            </div>
            {streaming ? (
              <button className="chat-widget-send" onClick={handleStop} title="Stop">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2" /></svg>
              </button>
            ) : (
              <button className="chat-widget-send" onClick={handleSend} disabled={loading || !input.trim()} title="Send">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="22" y1="2" x2="11" y2="13" /><polygon points="22 2 15 22 11 13 2 9 22 2" />
                </svg>
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}
