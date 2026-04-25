import { useEffect, useState } from 'react';
import { api } from '../../api/client';

interface SendToTicketPickerProps {
  /** If provided, sends a KB article by document ID */
  documentId?: number;
  /** If provided, sends raw text content as a reply */
  content?: string;
  /** Button label (defaults to "Send to Ticket") */
  label?: string;
  /** Extra CSS class on the trigger button */
  className?: string;
  /** Size variant */
  size?: 'xs' | 'sm';
}

/**
 * Inline ticket picker — searches open tickets and sends content as a reply.
 * Supports two modes:
 *   1. documentId — sends a KB article via /kb/send-to-ticket
 *   2. content — sends raw text via /ai/send-to-ticket
 */
export function SendToTicketPicker({ documentId, content, label, className, size = 'sm' }: SendToTicketPickerProps) {
  const [open, setOpen] = useState(false);
  const [tickets, setTickets] = useState<any[]>([]);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState<number | null>(null);
  const [sent, setSent] = useState<Set<number>>(new Set());

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    const params: Record<string, string> = { limit: '10' };
    if (search) params.search = search;
    api.listTickets(params)
      .then((res) => setTickets(res.tickets))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [open, search]);

  const handleSend = async (ticketId: number) => {
    setSending(ticketId);
    try {
      if (documentId) {
        await api.sendArticleToTicket(documentId, ticketId);
      } else if (content) {
        await api.sendChatResponseToTicket(content, ticketId);
      }
      setSent((prev) => new Set(prev).add(ticketId));
    } catch {}
    setSending(null);
  };

  const btnSize = size === 'xs' ? 'btn-xs' : 'btn-sm';

  if (!open) {
    return (
      <button className={`btn ${btnSize} btn-primary ${className || ''}`} onClick={() => setOpen(true)}>
        {label || 'Send to Ticket'}
      </button>
    );
  }

  return (
    <div className="send-to-ticket-picker">
      <div className="send-to-ticket-header">
        <input
          className="form-input form-input-sm"
          type="text"
          placeholder="Search tickets..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          autoFocus
        />
        <button className="btn btn-xs btn-ghost" onClick={() => setOpen(false)}>&times;</button>
      </div>
      <div className="send-to-ticket-list">
        {loading ? (
          <div className="send-to-ticket-empty">Loading...</div>
        ) : tickets.length === 0 ? (
          <div className="send-to-ticket-empty">No tickets found</div>
        ) : (
          tickets.map((t) => (
            <div key={t.id} className="send-to-ticket-row">
              <div className="send-to-ticket-info">
                <span className="mono-text">{t.ticket_number}</span>
                <span className="send-to-ticket-subject">{t.subject}</span>
              </div>
              <button
                className={`btn btn-xs ${sent.has(t.id) ? 'btn-ghost' : 'btn-primary'}`}
                disabled={sending === t.id || sent.has(t.id)}
                onClick={() => handleSend(t.id)}
              >
                {sent.has(t.id) ? 'Sent' : sending === t.id ? '...' : 'Send'}
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
