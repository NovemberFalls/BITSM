import { useState, useEffect } from 'react';
import { api } from '../../api/client';
import type { Ticket } from '../../types';
import { STATUS_OPTIONS } from '../../types';

interface PortalSearchProps {
  query: string;
  onViewTicket: (id: number) => void;
  onClose: () => void;
}

interface SearchResults {
  tickets: Ticket[];
  articles: { id: number; title: string; source_url: string; module_slug: string }[];
}

export function PortalSearch({ query, onViewTicket, onClose }: PortalSearchProps) {
  const [results, setResults] = useState<SearchResults>({ tickets: [], articles: [] });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    Promise.all([
      api.listTickets({ search: query }).catch(() => ({ tickets: [], total: 0 })),
      api.listDocuments({ search: query }).catch(() => ({ documents: [], total: 0 })),
    ]).then(([ticketRes, docRes]) => {
      if (cancelled) return;
      setResults({
        tickets: ticketRes.tickets.slice(0, 5),
        articles: (docRes.documents || []).slice(0, 5),
      });
      setLoading(false);
    });

    return () => { cancelled = true; };
  }, [query]);

  const total = results.tickets.length + results.articles.length;

  return (
    <div className="portal-search-results">
      <div className="portal-search-header">
        <span className="portal-search-query">Results for "{query}"</span>
        <button className="btn btn-ghost btn-sm" onClick={onClose}>Clear</button>
      </div>

      {loading && <div className="portal-search-loading">Searching...</div>}

      {!loading && total === 0 && (
        <div className="portal-search-empty">No results found. Try a different search term.</div>
      )}

      {!loading && results.tickets.length > 0 && (
        <div className="portal-search-section">
          <h4 className="portal-search-section-title">My Tickets</h4>
          {results.tickets.map((t) => (
            <div key={t.id} className="portal-search-item" onClick={() => onViewTicket(t.id)}>
              <span className="mono-text">{t.ticket_number}</span>
              <span className="portal-search-item-title">{t.subject}</span>
              <span className={`badge badge-${t.status}`}>
                {STATUS_OPTIONS.find((s) => s.value === t.status)?.label || t.status}
              </span>
            </div>
          ))}
        </div>
      )}

      {!loading && results.articles.length > 0 && (
        <div className="portal-search-section">
          <h4 className="portal-search-section-title">Help Articles</h4>
          {results.articles.map((a) => (
            <a
              key={a.id}
              className="portal-search-item"
              href={a.source_url || '#'}
              target={a.source_url ? '_blank' : undefined}
              rel="noopener noreferrer"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" />
              </svg>
              <span className="portal-search-item-title">{a.title}</span>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}
