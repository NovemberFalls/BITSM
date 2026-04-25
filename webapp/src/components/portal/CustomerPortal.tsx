import { useEffect, useRef, useState, useCallback } from 'react';
import { useTicketStore } from '../../store/ticketStore';
import { useHierarchyStore } from '../../store/hierarchyStore';
import { useAuthStore } from '../../store/authStore';
import { useThemeStore } from '../../store/themeStore';
import type { Ticket, PortalCard } from '../../types';
import { STATUS_OPTIONS, PRIORITY_OPTIONS } from '../../types';
import { CascadingSelect } from '../common/CascadingSelect';
import { PortalHero } from './PortalHero';
import { PortalCardGrid } from './PortalCardGrid';
import { PortalSearch } from './PortalSearch';
import { ChatWidget } from './ChatWidget';
import { renderMarkdown } from '../../utils/markdown';
import { api } from '../../api/client';
import { ReplyToolbar } from '../common/ReplyToolbar';
import { StatusPage } from './StatusPage';
import { PortalKB } from './PortalKB';

type PortalView = 'landing' | 'tickets' | 'detail' | 'create' | 'status' | 'kb';

// Module-level cache for seamless revisits
let _cachedTickets: Ticket[] = [];
let _hasLoaded = false;

/** Read portal sub-view state from URL query params */
function getPortalStateFromURL(): { view: PortalView; ticketId: number | null } {
  const params = new URLSearchParams(window.location.search);
  const v = params.get('view');
  const id = params.get('id');
  if (v === 'tickets') return { view: 'tickets', ticketId: null };
  if (v === 'create') return { view: 'create', ticketId: null };
  if (v === 'status') return { view: 'status', ticketId: null };
  if (v === 'kb') return { view: 'kb', ticketId: null };
  if (v === 'detail' && id) return { view: 'detail', ticketId: parseInt(id, 10) || null };
  return { view: 'landing', ticketId: null };
}

/** Push portal sub-view state to URL without full reload */
function pushPortalState(view: PortalView, ticketId?: number | null) {
  const base = window.location.pathname;
  let qs = '';
  if (view === 'tickets') qs = '?view=tickets';
  else if (view === 'create') qs = '?view=create';
  else if (view === 'status') qs = '?view=status';
  else if (view === 'kb') qs = '?view=kb';
  else if (view === 'detail' && ticketId) qs = `?view=detail&id=${ticketId}`;
  // 'landing' → no query params
  window.history.pushState({ portalView: view, ticketId }, '', base + qs);
}

export function CustomerPortal() {
  const { tickets, loading, loadTickets, createTicket, activeTicket, activeComments, loadTicket, addComment } = useTicketStore();
  const { locations, problemCategories, loadAll } = useHierarchyStore();
  const user = useAuthStore((s) => s.user);

  // Initialize state from URL params (persists across refresh)
  const initial = getPortalStateFromURL();
  const [view, setViewRaw] = useState<PortalView>(initial.view);
  const [selectedTicketId, setSelectedTicketId] = useState<number | null>(initial.ticketId);
  const [searchQuery, setSearchQuery] = useState<string | null>(null);
  const [defaultTeamId, setDefaultTeamId] = useState<number | null>(null);

  // Wrap setView to also push URL state
  const setView = (v: PortalView, ticketId?: number | null) => {
    setViewRaw(v);
    pushPortalState(v, ticketId);
  };

  // Restore ticket detail if URL had ?view=detail&id=X on mount
  useEffect(() => {
    if (initial.view === 'detail' && initial.ticketId) {
      loadTicket(initial.ticketId);
    }
  }, []);

  // Handle browser back/forward
  useEffect(() => {
    const onPopState = () => {
      const s = getPortalStateFromURL();
      setViewRaw(s.view);
      setSelectedTicketId(s.ticketId);
      if (s.view === 'detail' && s.ticketId) loadTicket(s.ticketId);
    };
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  const config = window.__APP_CONFIG__;
  const tenantSettings = config?.tenant_settings || {};
  const aiChatEnabled = config?.ai_chat_enabled || false;
  const problemFieldLabel = tenantSettings.problem_field_label || 'Problem Category';
  const greeting = tenantSettings.portal_greeting || 'How can we help you today?';
  const background = tenantSettings.portal_background || 'gradient-indigo';
  const cardOpacity = tenantSettings.portal_card_opacity ?? 70;
  const logoUrl = tenantSettings.portal_logo_url || '';

  // Dark mode from theme store (single source of truth)
  const isDark = useThemeStore((s) => s.mode) === 'dark';

  useEffect(() => {
    if (!_hasLoaded) {
      loadAll();
      _hasLoaded = true;
    }
    // Always refresh tickets on mount (no flash — store already has data)
    loadTickets();
  }, []);

  // Poll ticket list every 15s for seamless updates (landing + tickets views)
  useEffect(() => {
    if (view !== 'landing' && view !== 'tickets') return;
    const interval = setInterval(() => { loadTickets(); }, 15000);
    return () => clearInterval(interval);
  }, [view]);

  // Keep cache in sync (always, including empty — so purged tickets don't linger)
  useEffect(() => {
    _cachedTickets = tickets;
  }, [tickets]);

  // Sort: open/pending first, then by newest
  const rawTickets = tickets.length > 0 ? tickets : _cachedTickets;
  const displayTickets = [...rawTickets].sort((a, b) => {
    const activeA = (a.status === 'open' || a.status === 'pending') ? 0 : 1;
    const activeB = (b.status === 'open' || b.status === 'pending') ? 0 : 1;
    if (activeA !== activeB) return activeA - activeB;
    return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
  });

  const handleViewTicket = (id: number) => {
    setSelectedTicketId(id);
    loadTicket(id);
    setView('detail', id);
    setSearchQuery(null);
  };

  const handleBack = () => {
    setView('landing');
    setSelectedTicketId(null);
    setSearchQuery(null);
  };

  const handleCardAction = (card: PortalCard) => {
    switch (card.action) {
      case 'create_ticket':
        setView('create');
        setSearchQuery(null);
        setDefaultTeamId(card.default_team_id ?? null);
        break;
      case 'my_tickets':
        setView('tickets');
        setSearchQuery(null);
        break;
      case 'kb':
        setView('kb');
        setSearchQuery(null);
        break;
      case 'chat':
        window.location.href = '/chat';
        break;
      case 'status':
        setView('status');
        setSearchQuery(null);
        break;
      case 'url':
        if (card.url) window.open(card.url, '_blank');
        break;
    }
  };

  const handleSearch = (query: string) => {
    setSearchQuery(query);
  };

  return (
    <div className={`portal-landing-wrapper ${isDark ? 'portal-dark' : ''}`} style={{ '--portal-glass-opacity': (cardOpacity / 100).toFixed(2) } as React.CSSProperties}>
      {logoUrl && (
        <div className="portal-logo-overlay">
          <img src={logoUrl} alt="" />
        </div>
      )}
      {view === 'landing' && (
        <div className="portal-landing">
          <PortalHero greeting={greeting} background={background} onSearch={handleSearch} />

          {searchQuery ? (
            <PortalSearch
              query={searchQuery}
              onViewTicket={handleViewTicket}
              onClose={() => setSearchQuery(null)}
            />
          ) : (
            <>
              <PortalCardGrid
                cards={tenantSettings.portal_cards}
                aiChatEnabled={aiChatEnabled}
                onAction={handleCardAction}
                cardOpacity={cardOpacity}
              />

              <div className="portal-recent">
                <div className="portal-recent-header">
                  <h3 className="portal-recent-title">My Open Cases</h3>
                  <button className="btn btn-ghost btn-sm" onClick={() => setView('tickets')}>
                    View All
                  </button>
                </div>
                {(() => {
                  const openTickets = displayTickets.filter(t => t.status === 'open' || t.status === 'pending');
                  return openTickets.length === 0 && !loading ? (
                    <div className="portal-recent-empty">No open cases. All clear!</div>
                  ) : (
                  <div className="portal-ticket-list">
                    {openTickets.slice(0, 5).map((t) => (
                      <PortalTicketCard key={t.id} ticket={t} onClick={() => handleViewTicket(t.id)} />
                    ))}
                  </div>
                  );
                })()}
              </div>
            </>
          )}
        </div>
      )}

      {view === 'tickets' && (
        <div className="portal-landing">
          <div className="portal-subview-header">
            <button className="btn btn-ghost btn-sm" onClick={handleBack}>&larr; Back</button>
            <h2 className="portal-subview-title">My Tickets</h2>
            <button className="btn btn-primary btn-sm" onClick={() => setView('create')}>+ New Case</button>
          </div>
          <PortalTicketList tickets={displayTickets} loading={loading} onView={handleViewTicket} />
        </div>
      )}

      {view === 'detail' && selectedTicketId && (
        <div className="portal-landing portal-landing--detail">
          <div className="portal-subview-header">
            <button className="btn btn-ghost btn-sm" onClick={handleBack}>&larr; Back</button>
            <h2 className="portal-subview-title">Case Details</h2>
            <div />
          </div>
          <PortalTicketDetail
            ticket={activeTicket}
            comments={activeComments}
            ticketId={selectedTicketId}
            onAddComment={addComment}
          />
        </div>
      )}

      {view === 'create' && (
        <div className="portal-landing">
          <div className="portal-subview-header">
            <button className="btn btn-ghost btn-sm" onClick={handleBack}>&larr; Back</button>
            <h2 className="portal-subview-title">Submit a New Request</h2>
            <div />
          </div>
          <PortalCreateForm
            locations={locations}
            problemCategories={problemCategories}
            problemFieldLabel={problemFieldLabel}
            defaultTeamId={defaultTeamId}
            onCreated={() => { setView('landing'); _hasLoaded = false; loadTickets(); setDefaultTeamId(null); }}
            onCancel={handleBack}
            createTicket={createTicket}
          />
        </div>
      )}

      {view === 'status' && (
        <div className="portal-landing">
          <StatusPage onBack={handleBack} />
        </div>
      )}

      {view === 'kb' && (
        <div className="portal-landing">
          <PortalKB onBack={handleBack} />
        </div>
      )}

      {/* AI Chat Widget */}
      {aiChatEnabled && <ChatWidget />}
    </div>
  );
}


function PortalTicketCard({ ticket, onClick }: { ticket: Ticket; onClick: () => void }) {
  return (
    <div className="portal-ticket-card" onClick={onClick}>
      <div className="portal-ticket-top">
        <span className="mono-text">{ticket.ticket_number}</span>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          {ticket.first_response_at ? (
            <span className="badge badge-responded">Responded</span>
          ) : ticket.status === 'open' ? (
            <span className="badge badge-awaiting">Awaiting Response</span>
          ) : null}
          <span className={`badge badge-${ticket.status}`}>
            {STATUS_OPTIONS.find((s) => s.value === ticket.status)?.label || ticket.status}
          </span>
        </div>
      </div>
      <div className="portal-ticket-subject">{ticket.subject}</div>
      <div className="portal-ticket-bottom">
        <span className={`badge badge-${ticket.priority}`}>
          {PRIORITY_OPTIONS.find((p) => p.value === ticket.priority)?.label || ticket.priority}
        </span>
        <span className="portal-ticket-date">{new Date(ticket.created_at).toLocaleDateString()}</span>
      </div>
    </div>
  );
}


function PortalTicketList({
  tickets, loading, onView,
}: {
  tickets: Ticket[];
  loading: boolean;
  onView: (id: number) => void;
}) {
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [searchFilter, setSearchFilter] = useState<string>('');

  if (loading && tickets.length === 0) {
    return <div className="portal-recent-empty">Loading cases...</div>;
  }

  if (tickets.length === 0) {
    return <div className="portal-recent-empty">No cases yet. Click "+ New Case" to submit a request.</div>;
  }

  const filtered = tickets.filter((t) => {
    if (statusFilter && t.status !== statusFilter) return false;
    if (searchFilter && !t.subject.toLowerCase().includes(searchFilter.toLowerCase())) return false;
    return true;
  });

  return (
    <div>
      <div className="portal-filter-bar">
        <input
          type="text"
          className="form-input portal-filter-search"
          placeholder="Search cases..."
          value={searchFilter}
          onChange={(e) => setSearchFilter(e.target.value)}
        />
        <select
          className="form-input form-select portal-filter-select"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
        >
          <option value="">All Statuses</option>
          {STATUS_OPTIONS.map((s) => (
            <option key={s.value} value={s.value}>{s.label}</option>
          ))}
        </select>
        <span className="portal-filter-count">{filtered.length} of {tickets.length} cases</span>
      </div>
      <div className="portal-ticket-list">
        {filtered.map((t) => (
          <PortalTicketCard key={t.id} ticket={t} onClick={() => onView(t.id)} />
        ))}
        {filtered.length === 0 && (
          <div className="portal-recent-empty">No cases match your filters.</div>
        )}
      </div>
    </div>
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

function PortalTicketDetail({
  ticket, comments, ticketId, onAddComment,
}: {
  ticket: Ticket | null;
  comments: any[];
  ticketId: number;
  onAddComment: (ticketId: number, content: string, isInternal?: boolean, sendEmail?: boolean, cc?: string[], attachmentIds?: number[]) => Promise<void>;
}) {
  const [comment, setComment] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [descExpanded, setDescExpanded] = useState(false);
  const [customFields, setCustomFields] = useState<any[]>([]);
  const refreshComments = useTicketStore((s) => s.refreshComments);
  const commentsContainerRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const prevCountRef = useRef(0);

  // Load custom fields for this ticket (customer-facing only, filtered server-side)
  useEffect(() => {
    if (!ticketId) return;
    api.getTicket(ticketId).then((res: any) => {
      setCustomFields(res.custom_fields || []);
    }).catch(() => {});
  }, [ticketId]);

  // Poll for new comments every 5 seconds (seamless update for Atlas replies)
  useEffect(() => {
    if (!ticketId) return;
    const interval = setInterval(() => { refreshComments(ticketId); }, 5000);
    return () => clearInterval(interval);
  }, [ticketId, refreshComments]);

  // Only auto-scroll when NEW comments arrive after initial load
  const visibleComments = comments.filter(c => !c.is_internal);
  useEffect(() => {
    if (prevCountRef.current > 0 && visibleComments.length > prevCountRef.current) {
      const el = commentsContainerRef.current;
      if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
    }
    prevCountRef.current = visibleComments.length;
  }, [visibleComments.length]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if ((!comment.trim() && pendingFiles.length === 0) || submitting) return;
    setSubmitting(true);
    try {
      // Upload files first if any
      let attachmentIds: number[] = [];
      if (pendingFiles.length > 0) {
        setUploading(true);
        for (const file of pendingFiles) {
          const result = await api.uploadAttachment(ticketId, file);
          attachmentIds.push(result.id);
        }
        setUploading(false);
      }
      await onAddComment(ticketId, comment.trim() || '(attachment)', false, false, [], attachmentIds);
      setComment('');
      setPendingFiles([]);
    } catch {}
    setSubmitting(false);
  };

  const handleFilePick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    setPendingFiles((prev) => [...prev, ...files]);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const removeFile = (index: number) => {
    setPendingFiles((prev) => prev.filter((_, i) => i !== index));
  };

  if (!ticket || ticket.id !== ticketId) {
    return <div className="portal-recent-empty">Loading case...</div>;
  }

  const isResolved = ticket.status === 'resolved' || ticket.status === 'closed_not_resolved';

  return (
    <div className="portal-detail">
      <div className="portal-detail-header">
        <span className="mono-text">{ticket.ticket_number}</span>
        <span className={`badge badge-${ticket.status}`}>
          {STATUS_OPTIONS.find((s) => s.value === ticket.status)?.label || ticket.status}
        </span>
        <span className={`badge badge-${ticket.priority}`}>
          {PRIORITY_OPTIONS.find((p) => p.value === ticket.priority)?.label || ticket.priority}
        </span>
        {ticket.first_response_at ? (
          <span className="badge badge-responded">Responded</span>
        ) : ticket.status === 'open' ? (
          <span className="badge badge-awaiting">Awaiting Response</span>
        ) : null}
      </div>
      <h3 className="portal-detail-subject">{ticket.subject}</h3>

      {ticket.description && (
        <div
          className="portal-detail-description"
          style={!descExpanded ? { maxHeight: 120, overflow: 'hidden', position: 'relative', cursor: 'pointer' } : { cursor: 'pointer' }}
          onClick={() => setDescExpanded(!descExpanded)}
        >
          <div dangerouslySetInnerHTML={{ __html: renderMarkdown(ticket.description) }} />
          {!descExpanded && ticket.description.length > 200 && (
            <div style={{
              position: 'absolute', bottom: 0, left: 0, right: 0, height: 40,
              background: 'linear-gradient(transparent, var(--t-panel-alt) 70%)',
              display: 'flex', alignItems: 'flex-end', justifyContent: 'center',
              paddingBottom: 4, fontSize: 11, color: 'var(--t-accent-text)',
            }}>
              Click to expand
            </div>
          )}
        </div>
      )}

      {(ticket.location_breadcrumb || ticket.problem_category_breadcrumb) && (
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
          {ticket.location_breadcrumb && (
            <div className="portal-detail-meta">
              <span className="detail-meta-label">Location:</span> {ticket.location_breadcrumb}
            </div>
          )}
          {ticket.problem_category_breadcrumb && (
            <div className="portal-detail-meta">
              <span className="detail-meta-label">Category:</span> {ticket.problem_category_breadcrumb}
            </div>
          )}
        </div>
      )}

      {customFields.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 8 }}>
          <div style={{ fontSize: 10, fontWeight: 600, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--t-text-dim)' }}>
            Additional Information
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
            {customFields.map((f: any) => {
              let display: string;
              const val = f.current_value;
              if (f.field_type === 'checkbox') display = val ? 'Yes' : 'No';
              else if (f.field_type === 'multi_select' && Array.isArray(val)) display = val.join(', ');
              else if (f.field_type === 'date' && val) display = new Date(val).toLocaleDateString();
              else display = String(val ?? '');
              return (
                <div key={f.id} className="portal-detail-meta">
                  <span className="detail-meta-label">{f.name}:</span> {display}
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className="portal-detail-comments" ref={commentsContainerRef}>
        <h4 className="detail-section-title">Updates</h4>
        {visibleComments.length === 0 && (
          <div className="comment-empty">No updates yet</div>
        )}
        {visibleComments.map((c) => {
          const isRequester = c.author_id === ticket.requester_id;
          return (
            <div key={c.id} className={`comment-item ${isRequester ? 'comment-item-requester' : 'comment-item-other'}`}>
              <div className="comment-header">
                <span className="comment-author">{c.author_name || 'Atlas'}</span>
                <span className="comment-time">{new Date(c.created_at).toLocaleString()}</span>
              </div>
              <div className="comment-body chat-markdown" dangerouslySetInnerHTML={{ __html: renderMarkdown(c.content || '') }} />
              {c.attachments && c.attachments.length > 0 && (
                <div className="comment-attachments">
                  {c.attachments.map((att: any) => {
                    const isImg = att.content_type?.startsWith('image/');
                    if (isImg) {
                      return <img key={att.id} className="comment-attachment-img" src={`/api/tickets/${ticketId}/attachments/${att.id}`} alt={att.filename} onClick={() => window.open(`/api/tickets/${ticketId}/attachments/${att.id}`, '_blank')} />;
                    }
                    return (
                      <a key={att.id} className="comment-attachment" href={`/api/tickets/${ticketId}/attachments/${att.id}`} target="_blank" rel="noopener noreferrer">
                        <span className="comment-attachment-icon">📎</span>
                        {att.filename} <span style={{ color: 'var(--t-text-dim)', fontSize: 11 }}>({formatSize(att.file_size)})</span>
                      </a>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {!isResolved && (
        <form onSubmit={handleSubmit} className="portal-comment-form">
          <input type="file" ref={fileInputRef} style={{ display: 'none' }} multiple onChange={handleFilePick} accept="image/*,.pdf,.txt,.log,.csv,.xlsx,.doc,.docx" />
          <ReplyToolbar
            textareaRef={textareaRef}
            setText={setComment}
            getCurrentText={() => comment}
            onAttach={() => fileInputRef.current?.click()}
            hint="Ctrl+Enter to send"
          />
          <textarea
            ref={textareaRef}
            className="form-input form-textarea has-toolbar"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Add a reply..."
            rows={3}
          />
          {pendingFiles.length > 0 && (
            <div className="attachment-chips">
              {pendingFiles.map((f, i) => (
                <span key={i} className="attachment-chip">
                  <span className="attachment-chip-name">{f.name}</span>
                  <span className="attachment-chip-size">{formatSize(f.size)}</span>
                  <span className="attachment-chip-remove" onClick={() => removeFile(i)}>&times;</span>
                </span>
              ))}
            </div>
          )}
          <div className="form-actions">
            <button type="submit" className="btn btn-primary" disabled={submitting || (!comment.trim() && pendingFiles.length === 0)}>
              {uploading ? 'Uploading...' : submitting ? 'Sending...' : 'Reply'}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

const PORTAL_TYPE_LABELS: Record<string, string> = {
  support: 'Support Case', task: 'Task', bug: 'Bug Report', feature: 'Feature Request', custom: 'Custom Request',
};

function PortalCreateForm({
  locations, problemCategories, problemFieldLabel, defaultTeamId, onCreated, onCancel, createTicket,
}: {
  locations: any[];
  problemCategories: any[];
  problemFieldLabel: string;
  defaultTeamId?: number | null;
  onCreated: () => void;
  onCancel: () => void;
  createTicket: (data: any) => Promise<any>;
}) {
  // Read initial type + template from URL params for shareable links
  const urlParams = new URLSearchParams(window.location.search);
  const initialType = (['support', 'task', 'bug', 'feature', 'custom'] as const).includes(urlParams.get('type') as any)
    ? (urlParams.get('type') as 'support' | 'task' | 'bug' | 'feature' | 'custom')
    : 'support';
  const initialTemplateId = urlParams.get('template') ? parseInt(urlParams.get('template')!, 10) || null : null;

  const [ticketType, setTicketType] = useState<'support' | 'task' | 'bug' | 'feature' | 'custom'>(initialType);
  const [subject, setSubject] = useState('');
  const [description, setDescription] = useState('');
  const [locationId, setLocationId] = useState<number | null>(null);
  const [problemCategoryId, setProblemCategoryId] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Custom fields for selected category
  const [customFields, setCustomFields] = useState<any[]>([]);
  const [customValues, setCustomValues] = useState<Record<string, any>>({});
  const [loadingFields, setLoadingFields] = useState(false);

  // Form templates (for Custom type catalog)
  const [templates, setTemplates] = useState<any[]>([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState<number | null>(initialTemplateId);

  // Template search filter
  const [templateSearch, setTemplateSearch] = useState('');

  // Tenant form settings — supports per-type and legacy flat format
  const rawFormSettings = (window.__APP_CONFIG__ as any)?.tenant_settings?.ticket_form_settings || {};
  // Custom type defaults all built-in fields hidden (form is purely custom fields)
  const customDefaults = { subject_visible: false, subject_required: false, description_visible: false, description_required: false, location_visible: false, location_required: false, category_visible: false, category_required: false };
  const formSettings = (rawFormSettings[ticketType] && typeof rawFormSettings[ticketType] === 'object')
    ? rawFormSettings[ticketType]
    : ticketType === 'custom' ? customDefaults : rawFormSettings;
  const subjectRequired = formSettings.subject_required !== false; // default true
  const subjectVisible = formSettings.subject_visible !== false;
  const descriptionRequired = !!formSettings.description_required;
  const descriptionVisible = formSettings.description_visible !== false;
  const locationRequired = !!formSettings.location_required;
  const locationVisible = formSettings.location_visible !== false;
  const categoryRequired = !!formSettings.category_required;
  const categoryVisible = formSettings.category_visible !== false;

  // Determine which ticket types the user can create (RBAC + admin config)
  const userPerms: string[] = (window.__APP_CONFIG__ as any)?.user?.permissions || [];
  const hasPerTypeConfig = (['support', 'task', 'bug', 'feature', 'custom'] as const).some(
    (t) => rawFormSettings[t] && typeof rawFormSettings[t] === 'object'
  );
  const availableTypes = hasPerTypeConfig
    ? (['support', 'task', 'bug', 'feature', 'custom'] as const).filter(
        (t) => userPerms.includes(`tickets.create.${t}`)
          && rawFormSettings[t] && typeof rawFormSettings[t] === 'object'
      )
    : (['support', 'task', 'bug', 'feature', 'custom'] as const).filter(
        (t) => userPerms.includes(`tickets.create.${t}`)
      );
  const showTypeSelector = availableTypes.length > 1;

  // Helper: update URL with type and template params via replaceState
  const updateUrlParams = useCallback((type: string | null, templateId: number | null) => {
    const base = window.location.pathname;
    const params = new URLSearchParams(window.location.search);
    if (type) params.set('type', type);
    else params.delete('type');
    if (templateId) params.set('template', String(templateId));
    else params.delete('template');
    const qs = params.toString();
    window.history.replaceState(window.history.state, '', base + (qs ? '?' + qs : ''));
  }, []);

  // Load templates when switching to custom type
  const initialTemplateIdRef = useRef(initialTemplateId);
  useEffect(() => {
    if (ticketType === 'custom') {
      api.request('GET', '/form-templates/catalog').then((res) => {
        setTemplates(res.all || []);
        // If we had an initial template from URL that hasn't been consumed yet, keep it
        const pending = initialTemplateIdRef.current;
        if (pending) {
          const exists = (res.all || []).some((t: any) => t.id === pending);
          if (exists) {
            setSelectedTemplateId(pending);
          }
          initialTemplateIdRef.current = null;
        }
      }).catch(() => {});
      // Only clear selection if NOT the initial mount with a URL template
      if (!initialTemplateIdRef.current) {
        setSelectedTemplateId(null);
      }
      setCustomFields([]);
      setCustomValues({});
    }
  }, [ticketType]);

  // Load template-specific fields when a template is selected
  useEffect(() => {
    if (ticketType === 'custom') {
      if (!selectedTemplateId) { setCustomFields([]); setCustomValues({}); return; }
      setLoadingFields(true);
      api.listCustomFieldsForForm({ form_template_id: selectedTemplateId, ticket_type: 'custom' })
        .then((res) => {
          const fields = res.fields || [];
          setCustomFields(fields);
          const init: Record<string, any> = {};
          for (const f of fields) {
            if (f.field_type === 'checkbox') init[f.field_key] = false;
            else if (f.field_type === 'multi_select') init[f.field_key] = [];
            else init[f.field_key] = '';
          }
          setCustomValues(init);
        })
        .catch(() => {})
        .finally(() => setLoadingFields(false));
      return;
    }
  }, [selectedTemplateId]);

  // Load custom fields when category changes (or on mount for global fields) — non-custom types
  useEffect(() => {
    if (ticketType === 'custom') return; // custom type uses template-driven loading above
    setCustomFields([]);
    setCustomValues({});
    let cancelled = false;
    setLoadingFields(true);
    const params: { category_id?: number; ticket_type?: string } = { ticket_type: ticketType };
    if (problemCategoryId) params.category_id = problemCategoryId;
    api.listCustomFieldsForForm(params)
      .then((res) => {
        if (cancelled) return;
        const fields = res.fields || [];
        setCustomFields(fields);
        // Initialize default values
        const init: Record<string, any> = {};
        for (const f of fields) {
          if (f.field_type === 'checkbox') init[f.field_key] = false;
          else if (f.field_type === 'multi_select') init[f.field_key] = [];
          else init[f.field_key] = '';
        }
        setCustomValues(init);
      })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoadingFields(false); });
    return () => { cancelled = true; };
  }, [problemCategoryId, ticketType]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    // Built-in field validation
    if (subjectRequired && !subject.trim()) { setError('Subject is required'); return; }
    if (descriptionRequired && !description.trim()) { setError('Description is required'); return; }
    if (locationRequired && !locationId) { setError('Location is required'); return; }
    if (categoryRequired && !problemCategoryId) { setError(`${problemFieldLabel} is required`); return; }

    // Custom field required-to-create validation (skip hidden conditional fields)
    const missingCustom: string[] = [];
    for (const f of customFields) {
      if (!f.is_required_to_create) continue;
      if (!isFieldVisible(f)) continue;
      const v = customValues[f.field_key];
      const empty = v === '' || v === null || v === undefined || (Array.isArray(v) && v.length === 0);
      if (empty) missingCustom.push(f.name);
    }
    if (missingCustom.length > 0) {
      setError(`Required fields missing: ${missingCustom.join(', ')}`);
      return;
    }

    setSubmitting(true);
    try {
      const payload: any = {
        subject: subject.trim(),
        description: description.trim(),
        location_id: locationId,
        problem_category_id: problemCategoryId,
        ticket_type: ticketType,
      };
      if (defaultTeamId) payload.team_id = defaultTeamId;
      if (selectedTemplateId) payload.form_template_id = selectedTemplateId;

      // Attach custom field values (only non-empty)
      const cfPayload: Record<string, any> = {};
      for (const f of customFields) {
        const v = customValues[f.field_key];
        const empty = v === '' || v === null || v === undefined || (Array.isArray(v) && v.length === 0);
        if (!empty) cfPayload[f.field_key] = v;
      }
      if (Object.keys(cfPayload).length > 0) payload.custom_fields = cfPayload;

      await createTicket(payload);
      onCreated();
    } catch (err: any) {
      setError(err.message || 'Failed to create case');
    } finally {
      setSubmitting(false);
    }
  };

  const updateCustomValue = (key: string, value: any) => {
    setCustomValues((prev) => ({ ...prev, [key]: value }));
  };

  const inputStyle: React.CSSProperties = {
    width: '100%', padding: '8px 12px',
    background: 'var(--t-bg)', border: '1px solid var(--t-border)', borderRadius: 6,
    color: 'var(--t-text)', fontSize: 13, outline: 'none', boxSizing: 'border-box',
  };

  // ── Field order from Form Designer ──
  const savedFieldOrder: string[] = formSettings.field_order || [];
  const defaultBuiltinKeys = ['subject', 'description', 'location', 'category'];
  const orderedBuiltinKeys = [
    ...savedFieldOrder.filter((k: string) => defaultBuiltinKeys.includes(k)),
    ...defaultBuiltinKeys.filter((k) => !savedFieldOrder.includes(k)),
  ];

  // ── Built-in field renderers ──
  const renderBuiltinField = (key: string) => {
    switch (key) {
      case 'subject':
        return subjectVisible ? (
          <div key="subject" className="form-group">
            <label className="form-label">
              Subject{subjectRequired && <span style={{ color: 'var(--t-warning)', marginLeft: 3 }}>*</span>}
            </label>
            <input className="form-input" type="text" value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="What do you need help with?" autoFocus />
          </div>
        ) : null;
      case 'description':
        return descriptionVisible ? (
          <div key="description" className="form-group">
            <label className="form-label">
              Description{descriptionRequired && <span style={{ color: 'var(--t-warning)', marginLeft: 3 }}>*</span>}
            </label>
            <textarea className="form-input form-textarea" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Please provide as much detail as possible..." rows={5} />
          </div>
        ) : null;
      case 'location':
        return locationVisible && locations.length > 0 ? (
          <div key="location" className="form-group">
            <label className="form-label">
              Location{locationRequired && <span style={{ color: 'var(--t-warning)', marginLeft: 3 }}>*</span>}
            </label>
            <CascadingSelect items={locations} value={locationId} onChange={setLocationId} placeholder="Select location..." />
          </div>
        ) : null;
      case 'category':
        return categoryVisible && problemCategories.length > 0 ? (
          <div key="category" className="form-group">
            <label className="form-label">
              {problemFieldLabel}{categoryRequired && <span style={{ color: 'var(--t-warning)', marginLeft: 3 }}>*</span>}
            </label>
            <CascadingSelect items={problemCategories} value={problemCategoryId} onChange={setProblemCategoryId} placeholder={`Select ${problemFieldLabel.toLowerCase()}...`} />
          </div>
        ) : null;
      default:
        return null;
    }
  };

  // Check if a child field's parent condition is met
  const isFieldVisible = (f: any): boolean => {
    if (!f.parent_field_id || !f.show_when) return true;
    const parent = customFields.find((p: any) => p.id === f.parent_field_id);
    if (!parent) return true;
    const parentVal = customValues[parent.field_key];
    const triggerVals = f.show_when.values || (f.show_when.value ? [f.show_when.value] : []);
    return triggerVals.some((v: string) => String(parentVal) === String(v));
  };

  // ── Custom field renderer ──
  const renderCustomField = (f: any) => {
    // Skip child fields whose parent condition isn't met
    if (!isFieldVisible(f)) return null;
    const val = customValues[f.field_key];
    const isReq = f.is_required_to_create;
    return (
      <div key={f.id} className="form-group">
        <label className="form-label">
          {f.name}{isReq && <span style={{ color: 'var(--t-warning)', marginLeft: 3 }}>*</span>}
        </label>
        {f.description && (
          <div style={{ fontSize: 11, color: 'var(--t-text-muted)', marginBottom: 4, marginTop: -4 }}>{f.description}</div>
        )}
        {f.field_type === 'text' && (
          <input style={inputStyle} value={val ?? ''} onChange={(e) => updateCustomValue(f.field_key, e.target.value)} placeholder={`Enter ${f.name.toLowerCase()}`} />
        )}
        {f.field_type === 'textarea' && (
          <textarea style={{ ...inputStyle, resize: 'vertical', fontFamily: 'inherit' }} rows={3} value={val ?? ''} onChange={(e) => updateCustomValue(f.field_key, e.target.value)} placeholder={`Enter ${f.name.toLowerCase()}`} />
        )}
        {f.field_type === 'number' && (
          <input style={inputStyle} type="number" value={val ?? ''} onChange={(e) => updateCustomValue(f.field_key, e.target.value === '' ? '' : Number(e.target.value))} placeholder="0" />
        )}
        {f.field_type === 'date' && (
          <input style={inputStyle} type="date" value={val ?? ''} onChange={(e) => updateCustomValue(f.field_key, e.target.value)} />
        )}
        {f.field_type === 'url' && (
          <input style={inputStyle} type="url" value={val ?? ''} onChange={(e) => updateCustomValue(f.field_key, e.target.value)} placeholder="https://" />
        )}
        {f.field_type === 'checkbox' && (
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 13, color: 'var(--t-text)' }}>
            <input type="checkbox" checked={!!val} onChange={(e) => updateCustomValue(f.field_key, e.target.checked)} style={{ accentColor: 'var(--t-accent)', width: 15, height: 15 }} />
            {val ? 'Yes' : 'No'}
          </label>
        )}
        {f.field_type === 'select' && (
          <select style={{ ...inputStyle, cursor: 'pointer' }} value={val ?? ''} onChange={(e) => updateCustomValue(f.field_key, e.target.value || null)}>
            <option value="">— Select —</option>
            {(f.options || []).map((o: any) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        )}
        {f.field_type === 'multi_select' && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {(f.options || []).map((o: any) => {
              const checked = Array.isArray(val) && val.includes(o.value);
              return (
                <button key={o.value} type="button" onClick={() => {
                  const cur: string[] = Array.isArray(val) ? val : [];
                  updateCustomValue(f.field_key, checked ? cur.filter((v: string) => v !== o.value) : [...cur, o.value]);
                }} style={{
                  padding: '4px 12px', borderRadius: 20, fontSize: 12, cursor: 'pointer',
                  border: `1px solid ${checked ? 'var(--t-accent)' : 'var(--t-border)'}`,
                  background: checked ? 'color-mix(in srgb, var(--t-accent) 18%, transparent)' : 'var(--t-bg)',
                  color: checked ? 'var(--t-accent)' : 'var(--t-text-muted)',
                  fontWeight: checked ? 500 : 400, transition: 'all 0.15s',
                }}>
                  {o.label}
                </button>
              );
            })}
          </div>
        )}
      </div>
    );
  };

  return (
    <form onSubmit={handleSubmit} className="portal-create-form">
      {error && <div className="form-error">{error}</div>}

      {/* Ticket type selector — shown when admin has configured multiple types */}
      {showTypeSelector && (
        <div className="form-group">
          <label className="form-label">Request Type</label>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {availableTypes.map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => { setTicketType(t); setProblemCategoryId(null); setTemplateSearch(''); updateUrlParams(t === 'custom' ? 'custom' : null, null); }}
                style={{
                  padding: '6px 16px', borderRadius: 20, fontSize: 12, cursor: 'pointer',
                  border: `1.5px solid ${ticketType === t ? 'var(--t-accent)' : 'var(--t-border)'}`,
                  background: ticketType === t ? 'color-mix(in srgb, var(--t-accent) 15%, transparent)' : 'transparent',
                  color: ticketType === t ? 'var(--t-accent)' : 'var(--t-text-muted)',
                  fontWeight: ticketType === t ? 600 : 400, transition: 'all .15s',
                }}
              >
                {PORTAL_TYPE_LABELS[t]}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Template picker for Custom type */}
      {ticketType === 'custom' && templates.length > 0 && (
        <div className="form-group">
          <label className="form-label">Select Form</label>

          {/* Search filter — shown when 4+ templates */}
          {templates.length >= 4 && (
            <input
              type="text"
              value={templateSearch}
              onChange={(e) => setTemplateSearch(e.target.value)}
              placeholder="Search forms..."
              style={{
                width: '100%', padding: '6px 10px', marginBottom: 8,
                background: 'var(--t-bg)', border: '1px solid var(--t-border)', borderRadius: 6,
                color: 'var(--t-text)', fontSize: 12, outline: 'none', boxSizing: 'border-box',
              }}
            />
          )}

          {(() => {
            const query = templateSearch.trim().toLowerCase();
            const filtered = query
              ? templates.filter((t) => t.name.toLowerCase().includes(query))
              : templates;

            // When searching, render flat (no group headers)
            if (query) {
              const sorted = [...filtered].sort((a, b) => a.name.localeCompare(b.name));
              return (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {sorted.length === 0 && (
                    <div style={{ fontSize: 12, color: 'var(--t-text-muted)', padding: '4px 0' }}>No forms match your search.</div>
                  )}
                  {sorted.map((t) => (
                    <button
                      key={t.id}
                      type="button"
                      onClick={() => { setSelectedTemplateId(t.id); updateUrlParams('custom', t.id); }}
                      style={{
                        padding: '6px 16px', borderRadius: 20, fontSize: 12, cursor: 'pointer',
                        border: `1.5px solid ${selectedTemplateId === t.id ? 'var(--t-accent)' : 'var(--t-border)'}`,
                        background: selectedTemplateId === t.id ? 'color-mix(in srgb, var(--t-accent) 15%, transparent)' : 'transparent',
                        color: selectedTemplateId === t.id ? 'var(--t-accent)' : 'var(--t-text-muted)',
                        fontWeight: selectedTemplateId === t.id ? 600 : 400, transition: 'all .15s',
                      }}
                    >
                      {t.name}
                    </button>
                  ))}
                </div>
              );
            }

            // Grouped + alphabetically sorted view
            const grouped: Record<string, any[]> = {};
            for (const t of filtered) {
              const cat = t.catalog_category || 'Other';
              if (!grouped[cat]) grouped[cat] = [];
              grouped[cat].push(t);
            }
            // Sort groups alphabetically by category name
            const sortedGroups = Object.entries(grouped).sort(([a], [b]) => a.localeCompare(b));
            // Sort templates within each group alphabetically
            for (const [, items] of sortedGroups) {
              items.sort((a: any, b: any) => a.name.localeCompare(b.name));
            }
            return (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {sortedGroups.map(([cat, items]) => (
                  <div key={cat}>
                    {sortedGroups.length > 1 && (
                      <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--t-text-dim)', marginBottom: 4 }}>{cat}</div>
                    )}
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                      {items.map((t: any) => (
                        <button
                          key={t.id}
                          type="button"
                          onClick={() => { setSelectedTemplateId(t.id); updateUrlParams('custom', t.id); }}
                          style={{
                            padding: '6px 16px', borderRadius: 20, fontSize: 12, cursor: 'pointer',
                            border: `1.5px solid ${selectedTemplateId === t.id ? 'var(--t-accent)' : 'var(--t-border)'}`,
                            background: selectedTemplateId === t.id ? 'color-mix(in srgb, var(--t-accent) 15%, transparent)' : 'transparent',
                            color: selectedTemplateId === t.id ? 'var(--t-accent)' : 'var(--t-text-muted)',
                            fontWeight: selectedTemplateId === t.id ? 600 : 400, transition: 'all .15s',
                          }}
                        >
                          {t.name}
                        </button>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            );
          })()}
        </div>
      )}

      {/* Render fields in Form Designer order (built-in + custom interleaved) */}
      {orderedBuiltinKeys.map((key) => {
        const node = renderBuiltinField(key);
        // After rendering this builtin, check if any custom fields (cf:*) should appear here per field_order
        const cfKeysAfter = savedFieldOrder.length > 0
          ? (() => {
              const idx = savedFieldOrder.indexOf(key);
              if (idx === -1) return [];
              const after: string[] = [];
              for (let i = idx + 1; i < savedFieldOrder.length; i++) {
                if (savedFieldOrder[i].startsWith('cf:')) after.push(savedFieldOrder[i]);
                else break; // stop at next builtin
              }
              return after;
            })()
          : [];
        const cfNodes = cfKeysAfter.map((cfKey) => {
          const cfId = parseInt(cfKey.slice(3));
          const cf = customFields.find((f: any) => f.id === cfId);
          return cf ? renderCustomField(cf) : null;
        });
        return <>{node}{cfNodes}</>;
      })}

      {/* Custom fields not positioned via field_order — append at end */}
      {loadingFields && (
        <div style={{ fontSize: 12, color: 'var(--t-text-muted)', padding: '8px 0' }}>Loading additional fields…</div>
      )}
      {(() => {
        const positionedCfIds = new Set(
          savedFieldOrder.filter((k: string) => k.startsWith('cf:')).map((k: string) => parseInt(k.slice(3)))
        );
        const unpositioned = customFields.filter((f: any) => !positionedCfIds.has(f.id));
        if (unpositioned.length === 0) return null;
        return (
          <div style={{ marginTop: 4, marginBottom: 8, borderTop: '1px solid var(--t-border)', paddingTop: 14 }}>
            <div style={{ fontSize: 10, fontWeight: 600, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--t-text-muted)', marginBottom: 10 }}>
              Additional Information
            </div>
            {unpositioned.map(renderCustomField)}
          </div>
        );
      })()}

      <div className="portal-create-actions">
        <button type="button" className="btn btn-ghost" onClick={onCancel} disabled={submitting}>Cancel</button>
        <button type="submit" className="btn btn-primary" disabled={submitting}>
          {submitting ? 'Submitting...' : 'Submit Case'}
        </button>
      </div>
    </form>
  );
}
