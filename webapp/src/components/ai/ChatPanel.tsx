import { useState, useRef, useEffect, useCallback } from 'react';
import { useChatStore } from '../../store/chatStore';
import { useAuthStore } from '../../store/authStore';
import { renderMarkdown } from '../../utils/markdown';
import type { ChatMessage, ChatSource, MessageFeedback } from '../../types';
import { SendToTicketPicker } from '../common/SendToTicketPicker';
import { ReplyToolbar } from '../common/ReplyToolbar';
import { pushUrl } from '../../utils/url';
import { useUIStore } from '../../store/uiStore';

export function ChatPanel() {
  const {
    messages, loading, streaming, statusMessage, error, language, conversations,
    inactivityWarning, archived,
    sendMessage, stopStreaming, setLanguage, clearChat, loadConversations, resumeConversation, submitFeedback,
    clearInactivityTimers,
  } = useChatStore();

  const userRole = useAuthStore((s) => s.user?.role);
  const isAgent = userRole === 'super_admin' || userRole === 'tenant_admin' || userRole === 'agent';

  const [input, setInput] = useState('');
  const [showHistory, setShowHistory] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);

  // Auto-scroll when streaming and user is at bottom
  useEffect(() => {
    if (autoScroll) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages, statusMessage, autoScroll]);

  // Detect scroll position to toggle auto-scroll
  const handleScroll = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    setAutoScroll(atBottom);
  }, []);

  // Load conversation history on mount, clear timers on unmount
  useEffect(() => {
    loadConversations();
    return () => clearInactivityTimers();
  }, []);

  // Auto-grow textarea
  useEffect(() => {
    const ta = textareaRef.current;
    if (ta) {
      ta.style.height = 'auto';
      ta.style.height = Math.min(ta.scrollHeight, 120) + 'px';
    }
  }, [input]);

  const handleSend = () => {
    const q = input.trim();
    if (!q || loading) return;
    setInput('');
    setAutoScroll(true);
    sendMessage(q);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // BYOK gate: in demo mode without configured API keys, block the chat UI
  const config = window.__APP_CONFIG__;
  if (config?.demo_mode === true && config?.byok_configured !== true) {
    const navigateToBilling = () => {
      useUIStore.getState().setView('admin');
      pushUrl('/admin/billing');
    };
    return (
      <div
        className="chat-container"
        style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}
      >
        <div
          style={{
            textAlign: 'center',
            color: 'var(--t-text-muted)',
            fontSize: '14px',
            lineHeight: '1.6',
            maxWidth: '320px',
          }}
        >
          AI features require API keys. Configure your keys in{' '}
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
    <div className="chat-container">
      {/* Header */}
      <div className="chat-header">
        <div className="chat-header-left">
          <span className="chat-header-title">Atlas</span>
          <select
            className="chat-lang-select"
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
          >
            <option value="en">English</option>
            <option value="es">Español</option>
          </select>
        </div>
        <div className="chat-header-right">
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => setShowHistory(!showHistory)}
            title="History"
          >
            History
          </button>
          <button className="btn btn-ghost btn-sm" onClick={clearChat}>
            New Chat
          </button>
        </div>
      </div>

      {/* Conversation History Dropdown */}
      {showHistory && (
        <div className="chat-history-panel">
          <div className="chat-history-title">Recent Conversations</div>
          {conversations.length === 0 ? (
            <div className="chat-history-empty">No conversations yet</div>
          ) : (
            conversations.slice(0, 20).map((conv) => (
              <button
                key={conv.id}
                className="chat-history-item"
                onClick={() => {
                  resumeConversation(conv);
                  setShowHistory(false);
                }}
              >
                <span className="chat-history-item-text">
                  {conv.first_message || conv.messages?.[0]?.content || 'Conversation'}
                </span>
                <span className="chat-history-item-date">
                  {new Date(conv.created_at).toLocaleDateString()}
                </span>
              </button>
            ))
          )}
        </div>
      )}

      {/* Messages */}
      <div className="chat-messages" ref={containerRef} onScroll={handleScroll}>
        {messages.length === 0 && (
          <div className="empty-state">
            <div className="empty-state-icon">◎</div>
            <div className="empty-state-title">How can I make your day better?</div>
            <div className="empty-state-text">Ask me anything — I'll search your knowledge base for the best answer.</div>
          </div>
        )}
        {messages.map((msg, i) => (
          <MessageBubble
            key={i}
            message={msg}
            messageIndex={i}
            isStreaming={streaming && i === messages.length - 1 && msg.role === 'assistant'}
            isAgent={isAgent}
            onFeedback={submitFeedback}
          />
        ))}
        {statusMessage && (
          <div className="chat-status">
            <span className="chat-status-dot" />
            {statusMessage}
          </div>
        )}
        {error && (
          <div className="chat-error">Error: {error}</div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Inactivity warning */}
      {inactivityWarning && !archived && (
        <div className="chat-inactivity-warning">
          Chat will close in 2 minutes due to inactivity
        </div>
      )}

      {/* Archived overlay */}
      {archived ? (
        <div className="chat-archived-bar">
          <span>Conversation archived due to inactivity</span>
          <button className="btn btn-primary btn-sm" onClick={clearChat}>
            Start New Chat
          </button>
        </div>
      ) : (
        /* Input */
        <div className="chat-input-bar">
          <div className="chat-textarea-wrap">
            <ReplyToolbar textareaRef={textareaRef} setText={setInput} getCurrentText={() => input} />
            <textarea
              ref={textareaRef}
              className="chat-textarea has-toolbar"
              placeholder={language === 'es' ? 'Escribe tu pregunta...' : 'Type your question...'}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={loading}
              rows={1}
            />
          </div>
          {streaming ? (
            <button className="btn chat-stop-btn" onClick={stopStreaming}>
              Stop
            </button>
          ) : (
            <button className="btn btn-primary" onClick={handleSend} disabled={loading || !input.trim()}>
              Send
            </button>
          )}
        </div>
      )}
    </div>
  );
}


interface MessageBubbleProps {
  message: ChatMessage;
  messageIndex: number;
  isStreaming: boolean;
  isAgent: boolean;
  onFeedback: (messageIndex: number, rating: 'positive' | 'negative') => void;
}

function MessageBubble({ message, messageIndex, isStreaming, isAgent, onFeedback }: MessageBubbleProps) {
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const showFeedback = message.role === 'assistant' && message.content && !isStreaming;
  const showSendToTicket = isAgent && message.role === 'assistant' && message.content && !isStreaming;

  return (
    <div className={`chat-message ${message.role}`}>
      <div className="chat-message-content">
        <MarkdownContent text={message.content} />
        {isStreaming && <span className="chat-streaming-cursor" />}
      </div>
      {message.sources && message.sources.length > 0 && (
        <div className="chat-sources">
          <button className="chat-sources-toggle" onClick={() => setSourcesOpen(!sourcesOpen)}>
            {sourcesOpen ? '▾' : '▸'} {message.sources.length} source{message.sources.length !== 1 ? 's' : ''}
          </button>
          {sourcesOpen && (
            <div className="chat-sources-list">
              {message.sources.map((src, i) => (
                <SourceChip key={i} source={src} isAgent={isAgent} />
              ))}
            </div>
          )}
        </div>
      )}
      <div className="chat-message-actions">
        {showFeedback && (
          <div className="chat-feedback">
            <button
              className={`chat-feedback-btn ${message.feedback === 'positive' ? 'active positive' : ''}`}
              onClick={() => onFeedback(messageIndex, 'positive')}
              title="Helpful"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M7 10v12" /><path d="M15 5.88L14 10h5.83a2 2 0 011.92 2.56l-2.33 8A2 2 0 0117.5 22H4a2 2 0 01-2-2v-8a2 2 0 012-2h2.76a2 2 0 001.79-1.11L12 2a3.13 3.13 0 013 3.88z" />
              </svg>
            </button>
            <button
              className={`chat-feedback-btn ${message.feedback === 'negative' ? 'active negative' : ''}`}
              onClick={() => onFeedback(messageIndex, 'negative')}
              title="Not helpful"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M17 14V2" /><path d="M9 18.12L10 14H4.17a2 2 0 01-1.92-2.56l2.33-8A2 2 0 017.5 2H20a2 2 0 012 2v8a2 2 0 01-2 2h-2.76a2 2 0 00-1.79 1.11L12 22a3.13 3.13 0 01-3-3.88z" />
              </svg>
            </button>
          </div>
        )}
        {showSendToTicket && (
          <SendToTicketPicker content={message.content} label="Send to Ticket" size="xs" />
        )}
      </div>
    </div>
  );
}


function SourceChip({ source, isAgent }: { source: ChatSource; isAgent: boolean }) {
  return (
    <div className="chat-source-chip">
      <span className="chat-source-module">{source.module}</span>
      {source.url ? (
        <a href={source.url} target="_blank" rel="noopener noreferrer" className="chat-source-link">
          {source.title}
        </a>
      ) : (
        <span className="chat-source-title">{source.title}</span>
      )}
      {isAgent && source.document_id && (
        <SendToTicketPicker documentId={source.document_id} label="Send" size="xs" />
      )}
    </div>
  );
}


function MarkdownContent({ text }: { text: string }) {
  if (!text) return null;

  const html = renderMarkdown(text);
  return <div className="chat-markdown" dangerouslySetInnerHTML={{ __html: html }} />;
}


// renderMarkdown and escapeHtml imported from ../../utils/markdown
