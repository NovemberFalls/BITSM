import { useEffect, useState, useRef } from 'react';
import { useUIStore } from '../../store/uiStore';
import { useTicketStore } from '../../store/ticketStore';
import { api } from '../../api/client';
import { useHierarchyStore } from '../../store/hierarchyStore';
import type { TicketStatus, TicketPriority, AIFeatures } from '../../types';
import { STATUS_OPTIONS, PRIORITY_OPTIONS } from '../../types';
import { CascadingSelect } from '../common/CascadingSelect';
import { TagSuggestions } from '../common/TagSuggestions';
import { AtlasTab } from './AtlasTab';
import { timeAgo, formatDuration, formatSlaRemaining, slaStatusColor, slaStatusLabel } from '../../utils/time';
import { renderMarkdown } from '../../utils/markdown';
import { ReplyToolbar } from '../common/ReplyToolbar';
import { CustomFieldsPanel } from './CustomFieldsPanel';
import type { CustomFieldDefinition } from '../../types';

type CommentTab = 'all' | 'notes' | 'replies' | 'atlas' | 'timeline';
type ReplyMode = 'note' | 'reply';

interface ActivityEntry {
  id: number;
  activity_type: string;
  old_value: string | null;
  new_value: string | null;
  metadata: any;
  created_at: string;
  user_name: string | null;
}

export function TicketDetail({ onClose }: { onClose?: () => void } = {}) {
  const ticketDetailId = useUIStore((s) => s.ticketDetailId);
  const storeClose = useUIStore((s) => s.closeTicketDetail);
  const closeDetail = onClose || storeClose;
  const {
    activeTicket, activeComments, tagSuggestions, agents,
    loadTicket, updateTicket, addComment, loadAgents, refreshComments,
  } = useTicketStore();
  const { locations, problemCategories, loadAll } = useHierarchyStore();

  const [comment, setComment] = useState('');
  const commentRef = useRef<HTMLTextAreaElement>(null);
  const [replyMode, setReplyMode] = useState<ReplyMode>('reply');
  const [sendEmail, setSendEmail] = useState(true);
  const [ccInput, setCcInput] = useState('');
  const [submittingComment, setSubmittingComment] = useState(false);
  const [updatingStatus, setUpdatingStatus] = useState(false);
  const [commentTab, setCommentTab] = useState<CommentTab>('all');
  const [suggestedArticles, setSuggestedArticles] = useState<any[]>([]);
  const [sendingArticle, setSendingArticle] = useState<number | null>(null);
  const [sentArticles, setSentArticles] = useState<Set<number>>(new Set());
  const [articlesCollapsed, setArticlesCollapsed] = useState(false);
  const [descExpanded, setDescExpanded] = useState(false);
  const [incidentChildren, setIncidentChildren] = useState<any[]>([]);
  const [incidentParent, setIncidentParent] = useState<any>(null);
  const [showLinkModal, setShowLinkModal] = useState(false);
  const [linkSearch, setLinkSearch] = useState('');
  const [linkResults, setLinkResults] = useState<any[]>([]);
  const [linkSearching, setLinkSearching] = useState(false);
  const [allUsers, setAllUsers] = useState<{ id: number; name: string; email: string; role: string }[]>([]);

  // Dev item state
  const [workflowStatuses, setWorkflowStatuses] = useState<any[]>([]);
  const [subtasks, setSubtasks] = useState<any[]>([]);
  const [newSubtask, setNewSubtask] = useState('');
  const [workItemTypes, setWorkItemTypes] = useState<any[]>([]);
  const [sprints, setSprints] = useState<any[]>([]);
  const [children, setChildren] = useState<any[]>([]);
  const [rollup, setRollup] = useState<any>(null);
  const [editingAC, setEditingAC] = useState(false);
  const [acDraft, setAcDraft] = useState('');
  const [parentItem, setParentItem] = useState<any>(null);
  const [showParentSearch, setShowParentSearch] = useState(false);
  const [parentSearch, setParentSearch] = useState('');
  const [parentResults, setParentResults] = useState<any[]>([]);
  const [editingSubject, setEditingSubject] = useState(false);
  const [subjectDraft, setSubjectDraft] = useState('');
  const [editingDesc, setEditingDesc] = useState(false);
  const [descDraft, setDescDraft] = useState('');
  const [customFields, setCustomFields] = useState<CustomFieldDefinition[]>([]);
  const [activityLog, setActivityLog] = useState<ActivityEntry[]>([]);
  const [activityLoading, setActivityLoading] = useState(false);
  const [teams, setTeams] = useState<{ id: number; name: string }[]>([]);

  const loadCustomFields = async (id: number) => {
    try {
      const res = await api.getTicket(id);
      setCustomFields((res as any).custom_fields || []);
    } catch {}
  };

  useEffect(() => {
    if (ticketDetailId) {
      loadTicket(ticketDetailId);
      loadAll();
      loadAgents();
      api.listAllUsers().then(setAllUsers).catch(() => {});
      api.listTeams().then(setTeams).catch(() => {});
      api.suggestArticles(ticketDetailId).then(setSuggestedArticles).catch(() => {});
      // Load incident data
      api.getIncidentChildren(ticketDetailId).then((c) => setIncidentChildren(c || [])).catch(() => setIncidentChildren([]));
      // Load dev item data (types, sprints, subtasks)
      api.listWorkItemTypes().then(setWorkItemTypes).catch(() => {});
      api.listSprints({}).then(setSprints).catch(() => {});
      api.listTicketTasks(ticketDetailId).then(setSubtasks).catch(() => setSubtasks([]));
      api.getTicketChildren(ticketDetailId).then(setChildren).catch(() => setChildren([]));
      api.getTicketRollup(ticketDetailId).then(setRollup).catch(() => setRollup(null));
      loadCustomFields(ticketDetailId);
      // Re-fetch after pipeline has had time to process (tags, embeddings)
      const timer = setTimeout(() => {
        api.suggestArticles(ticketDetailId).then(setSuggestedArticles).catch(() => {});
      }, 12000);
      return () => clearTimeout(timer);
    }
  }, [ticketDetailId]);

  // Fetch activity log when timeline tab is selected
  useEffect(() => {
    if (commentTab === 'timeline' && ticketDetailId) {
      setActivityLoading(true);
      api.getTicketActivity(ticketDetailId)
        .then(setActivityLog)
        .catch(() => setActivityLog([]))
        .finally(() => setActivityLoading(false));
    }
  }, [commentTab, ticketDetailId]);

  // Load workflow statuses when ticket type is known
  useEffect(() => {
    const tt = activeTicket?.ticket_type;
    if (tt) {
      api.getWorkflowStatuses(tt).then(setWorkflowStatuses).catch(() => {});
    }
  }, [activeTicket?.ticket_type]);

  // Load parent item
  useEffect(() => {
    if (activeTicket?.parent_id) {
      api.getTicket(activeTicket.parent_id).then((p) => setParentItem(p?.ticket || p)).catch(() => setParentItem(null));
    } else {
      setParentItem(null);
    }
  }, [activeTicket?.parent_id]);

  // Poll for new comments every 5 seconds (seamless update for Atlas notes/replies)
  useEffect(() => {
    if (!ticketDetailId) return;
    const interval = setInterval(() => {
      refreshComments(ticketDetailId);
    }, 5000);
    return () => clearInterval(interval);
  }, [ticketDetailId, refreshComments]);

  // Check if this ticket is a child of an incident
  useEffect(() => {
    if (activeTicket?.parent_ticket_id) {
      api.getTicket(activeTicket.parent_ticket_id).then((p) => setIncidentParent(p?.ticket || p)).catch(() => setIncidentParent(null));
    } else {
      setIncidentParent(null);
    }
  }, [activeTicket?.parent_ticket_id]);

  // Incident link/unlink handlers
  const handleLinkSearch = async (q: string) => {
    setLinkSearch(q);
    if (q.length < 2) { setLinkResults([]); return; }
    setLinkSearching(true);
    try {
      const resp = await api.listTickets({ search: q, limit: '10' });
      const results = Array.isArray(resp) ? resp : (resp as any)?.tickets || [];
      setLinkResults(results.filter((t: any) => t.id !== ticketDetailId));
    } catch { setLinkResults([]); }
    setLinkSearching(false);
  };

  const handleLinkIncident = async (parentId: number) => {
    if (!ticketDetailId) return;
    await api.linkIncident(ticketDetailId, parentId);
    setShowLinkModal(false);
    setLinkSearch('');
    setLinkResults([]);
    loadTicket(ticketDetailId);
    api.getIncidentChildren(ticketDetailId).then((c) => setIncidentChildren(c || [])).catch(() => {});
  };

  const handleUnlinkIncident = async () => {
    if (!ticketDetailId) return;
    await api.unlinkIncident(ticketDetailId);
    setIncidentParent(null);
    loadTicket(ticketDetailId);
  };

  // Parent search for hierarchy linking
  const handleParentSearch = async (q: string) => {
    setParentSearch(q);
    if (q.length < 2) { setParentResults([]); return; }
    try {
      const resp = await api.listTickets({ search: q, ticket_type: 'task,bug,feature', limit: '10' });
      const results = Array.isArray(resp) ? resp : (resp as any)?.tickets || [];
      setParentResults(results.filter((t: any) => t.id !== ticketDetailId));
    } catch { setParentResults([]); }
  };

  const handleSetParent = async (parentId: number | null) => {
    if (!ticketDetailId) return;
    await updateTicket(ticketDetailId, { parent_id: parentId } as any);
    setShowParentSearch(false);
    setParentSearch('');
    setParentResults([]);
    loadTicket(ticketDetailId);
    if (parentId) {
      api.getTicket(parentId).then((p) => setParentItem(p?.ticket || p)).catch(() => {});
    } else {
      setParentItem(null);
    }
  };

  if (!ticketDetailId) return null;

  const ticket = activeTicket;
  const loading = !ticket || ticket.id !== ticketDetailId;

  // Filter comments by tab
  const filteredComments = activeComments.filter((c) => {
    if (commentTab === 'notes') return c.is_internal;
    if (commentTab === 'replies') return !c.is_internal;
    return true;
  });

  const [statusError, setStatusError] = useState<string | null>(null);
  const [highlightFields, setHighlightFields] = useState<string[]>([]);
  const [pendingStatus, setPendingStatus] = useState<string | null>(null);
  const displayStatus = pendingStatus ?? ticket?.status ?? '';
  const handleStatusChange = async (status: TicketStatus) => {
    if (!ticket || updatingStatus) return;
    setPendingStatus(status);
    setUpdatingStatus(true);
    setStatusError(null);
    setHighlightFields([]);
    try {
      await updateTicket(ticket.id, { status });
      setPendingStatus(null);
    } catch (e: any) {
      setStatusError(e?.message || 'Status change failed');
      const missing = e?.body?.missing_fields;
      if (Array.isArray(missing) && missing.length) setHighlightFields(missing);
      setPendingStatus(null); // revert to actual ticket status
      if (ticket) await loadTicket(ticket.id);
    }
    setUpdatingStatus(false);
  };

  const handlePriorityChange = async (priority: TicketPriority) => {
    if (!ticket) return;
    try { await updateTicket(ticket.id, { priority }); } catch {}
  };

  const handleAssigneeChange = async (assigneeId: string) => {
    if (!ticket) return;
    const value = assigneeId ? parseInt(assigneeId) : null;
    try { await updateTicket(ticket.id, { assignee_id: value } as any); } catch {}
  };

  const handleTeamChange = async (teamId: string) => {
    if (!ticket) return;
    const value = teamId ? parseInt(teamId) : null;
    try { await updateTicket(ticket.id, { team_id: value } as any); } catch {}
  };

  const handleRequesterChange = async (requesterId: string) => {
    if (!ticket) return;
    const value = requesterId ? parseInt(requesterId) : null;
    try { await updateTicket(ticket.id, { requester_id: value } as any); } catch {}
  };

  const handleLocationChange = async (locationId: number | null) => {
    if (!ticket) return;
    try { await updateTicket(ticket.id, { location_id: locationId }); } catch {}
  };

  const handleProblemCategoryChange = async (categoryId: number | null) => {
    if (!ticket) return;
    try { await updateTicket(ticket.id, { problem_category_id: categoryId }); } catch {}
  };

  const handleAddComment = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!comment.trim() || !ticket || submittingComment) return;
    setSubmittingComment(true);
    const isInternal = replyMode === 'note';
    const ccEmails = ccInput.split(',').map(s => s.trim()).filter(Boolean);
    try {
      await addComment(ticket.id, comment.trim(), isInternal, !isInternal && sendEmail, ccEmails);
      setComment('');
      setCcInput('');
    } catch {}
    setSubmittingComment(false);
  };

  const tenantSettings = (window.__APP_CONFIG__ as any)?.tenant_settings || {};
  const problemFieldLabel = tenantSettings.problem_field_label || 'Problem Category';
  const aiFeatures: AIFeatures = (window.__APP_CONFIG__ as any)?.ai_features || {};
  const userPermissions: string[] = (window.__APP_CONFIG__ as any)?.user?.permissions || [];
  const showAtlasTab = aiFeatures.agent_chat && userPermissions.includes('atlas.chat');

  if (loading) {
    return (
      <div className="ticket-workspace">
        <div className="ticket-workspace-header">
          <button className="btn btn-ghost btn-sm" onClick={closeDetail}>&larr; Back</button>
        </div>
        <div className="ticket-workspace-loading">Loading ticket...</div>
      </div>
    );
  }

  const isDevItem = ['task', 'bug', 'feature'].includes(ticket.ticket_type);
  const displayNumber = (ticket as any).work_item_number || ticket.ticket_number;
  const statusOptions = workflowStatuses.length > 0
    ? workflowStatuses.map((ws: any) => ({ value: ws.key, label: ws.label }))
    : STATUS_OPTIONS;
  const slaColor = slaStatusColor(ticket.sla_status);

  return (
    <div className="ticket-workspace">
      {/* Header */}
      <div className="ticket-workspace-header">
        <div className="ticket-workspace-header-left">
          <button className="btn btn-ghost btn-sm" onClick={closeDetail}>&larr; Back</button>
          <span className="ticket-number-label">{displayNumber}</span>
          {isDevItem && (ticket as any).work_item_number && (
            <span style={{ fontSize: 10, color: 'var(--t-text-dim)' }}>({ticket.ticket_number})</span>
          )}
        </div>
        <div className="ticket-workspace-header-right">
          <select
            className="form-input form-select form-select-sm"
            value={displayStatus}
            onChange={(e) => handleStatusChange(e.target.value as TicketStatus)}
            disabled={updatingStatus}
          >
            {statusOptions.map((s: any) => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Status error banner */}
      {statusError && (
        <div style={{
          margin: '0 0 2px 0', padding: '8px 14px', borderRadius: 6, fontSize: 12,
          background: 'rgba(239,68,68,0.12)', border: '1px solid rgba(239,68,68,0.3)',
          color: 'var(--t-status-urgent)', display: 'flex', alignItems: 'center', gap: 8,
        }}>
          <span style={{ flex: 1 }}>{statusError}</span>
          <button onClick={() => { setStatusError(null); setHighlightFields([]); }} style={{
            background: 'none', border: 'none', cursor: 'pointer',
            color: 'var(--t-text-muted)', fontSize: 14, padding: '0 2px',
          }}>&times;</button>
        </div>
      )}

      {/* Body: two columns */}
      <div className="ticket-workspace-body">
        {/* LEFT: Activity */}
        <div className="ticket-workspace-main">
          {/* Editable subject */}
          {editingSubject ? (
            <input
              className="form-input"
              style={{ fontSize: 18, fontWeight: 600, width: '100%', marginBottom: 8 }}
              value={subjectDraft}
              autoFocus
              onChange={(e) => setSubjectDraft(e.target.value)}
              onBlur={async () => {
                const trimmed = subjectDraft.trim();
                if (trimmed && trimmed !== ticket.subject) {
                  await updateTicket(ticket.id, { subject: trimmed } as any);
                }
                setEditingSubject(false);
              }}
              onKeyDown={async (e) => {
                if (e.key === 'Enter') {
                  const trimmed = subjectDraft.trim();
                  if (trimmed && trimmed !== ticket.subject) {
                    await updateTicket(ticket.id, { subject: trimmed } as any);
                  }
                  setEditingSubject(false);
                }
                if (e.key === 'Escape') setEditingSubject(false);
              }}
            />
          ) : (
            <h2
              className="ticket-workspace-subject"
              style={{ cursor: 'pointer' }}
              title="Click to rename"
              onClick={() => { setSubjectDraft(ticket.subject); setEditingSubject(true); }}
            >{ticket.subject}</h2>
          )}

          {/* Description — editable for dev items, read-only expandable for support */}
          {isDevItem ? (
            editingDesc ? (
              <div style={{ marginBottom: 12 }}>
                <textarea
                  className="form-input"
                  style={{ width: '100%', minHeight: 100, fontSize: 13, resize: 'vertical' }}
                  value={descDraft}
                  autoFocus
                  onChange={(e) => setDescDraft(e.target.value)}
                  placeholder="Add a description..."
                  onKeyDown={(e) => {
                    if (e.key === 'Escape') setEditingDesc(false);
                  }}
                />
                <div style={{ display: 'flex', gap: 6, marginTop: 6, justifyContent: 'flex-end' }}>
                  <button className="btn btn-ghost btn-sm" onClick={() => setEditingDesc(false)}>Cancel</button>
                  <button className="btn btn-primary btn-sm" onClick={async () => {
                    await updateTicket(ticket.id, { description: descDraft } as any);
                    setEditingDesc(false);
                  }}>Save</button>
                </div>
              </div>
            ) : (
              <div
                style={{ marginBottom: 12, cursor: 'pointer', minHeight: 32 }}
                title="Click to edit description"
                onClick={() => { setDescDraft(ticket.description || ''); setEditingDesc(true); }}
              >
                {ticket.description ? (
                  <div className="detail-description">
                    <div dangerouslySetInnerHTML={{ __html: renderMarkdown(ticket.description) }} />
                  </div>
                ) : (
                  <div style={{ fontSize: 13, color: 'var(--t-text-dim)', fontStyle: 'italic', padding: '8px 0' }}>
                    Click to add a description...
                  </div>
                )}
              </div>
            )
          ) : ticket.description ? (
            <div
              className="detail-description"
              style={!descExpanded ? { maxHeight: 120, overflow: 'hidden', position: 'relative', cursor: 'pointer' } : { cursor: 'pointer' }}
              onClick={() => setDescExpanded(!descExpanded)}
            >
              <div dangerouslySetInnerHTML={{ __html: renderMarkdown(ticket.description) }} />
              {!descExpanded && ticket.description.length > 200 && (
                <div style={{
                  position: 'absolute', bottom: 0, left: 0, right: 0, height: 40,
                  background: 'linear-gradient(transparent, var(--surface-1) 70%)',
                  display: 'flex', alignItems: 'flex-end', justifyContent: 'center',
                  paddingBottom: 4, fontSize: 11, color: 'var(--t-accent-text)',
                }}>
                  Click to expand
                </div>
              )}
            </div>
          ) : null}

          {/* Incident Linking */}
          {(incidentParent || incidentChildren.length > 0 || ticket.status === 'open' || ticket.status === 'pending') && (
            <div className="incident-section">
              {incidentParent && (
                <div className="incident-parent-bar">
                  <span className="incident-icon">🔗</span>
                  <span>Part of incident: </span>
                  <button className="incident-link" onClick={() => useUIStore.getState().openTicketDetail(incidentParent.id)}>
                    {incidentParent.ticket_number} — {incidentParent.subject}
                  </button>
                  <button className="btn btn-ghost btn-xs" onClick={handleUnlinkIncident}>Unlink</button>
                </div>
              )}
              {incidentChildren.length > 0 && (
                <div className="incident-children-bar">
                  <span className="incident-icon">📋</span>
                  <span>Incident group ({incidentChildren.length} related):</span>
                  <div className="incident-children-list">
                    {incidentChildren.map((child: any) => (
                      <button
                        key={child.id}
                        className="incident-child-chip"
                        onClick={() => useUIStore.getState().openTicketDetail(child.id)}
                      >
                        {child.ticket_number} <span className={`status-dot status-${child.status}`} />
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {!incidentParent && (ticket.status === 'open' || ticket.status === 'pending') && (
                <button className="btn btn-ghost btn-xs incident-link-btn" onClick={() => setShowLinkModal(true)}>
                  🔗 Link to Incident
                </button>
              )}
            </div>
          )}

          {/* Link to Incident Modal */}
          {showLinkModal && (
            <div className="modal-overlay" onClick={() => setShowLinkModal(false)}>
              <div className="modal-content modal-sm" onClick={(e) => e.stopPropagation()}>
                <div className="modal-header">
                  <h3>Link to Incident</h3>
                  <button className="btn btn-ghost btn-xs" onClick={() => setShowLinkModal(false)}>✕</button>
                </div>
                <div className="modal-body">
                  <input
                    className="form-input"
                    placeholder="Search by ticket number or subject..."
                    value={linkSearch}
                    onChange={(e) => handleLinkSearch(e.target.value)}
                    autoFocus
                  />
                  {linkSearching && <div className="incident-search-status">Searching...</div>}
                  <div className="incident-search-results">
                    {linkResults.map((t: any) => (
                      <button
                        key={t.id}
                        className="incident-search-result"
                        onClick={() => handleLinkIncident(t.id)}
                      >
                        <span className="incident-result-number">{t.ticket_number}</span>
                        <span className="incident-result-subject">{t.subject}</span>
                        <span className={`badge badge-${t.status}`}>{t.status}</span>
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Activity / Comments */}
          <div className="ticket-workspace-activity">
            <div className="comment-tabs">
              <button
                className={`comment-tab ${commentTab === 'replies' ? 'active' : ''}`}
                onClick={() => setCommentTab('replies')}
              >
                Replies <span className="comment-tab-count">{activeComments.filter(c => !c.is_internal).length}</span>
              </button>
              <button
                className={`comment-tab ${commentTab === 'notes' ? 'active' : ''}`}
                onClick={() => setCommentTab('notes')}
              >
                Agent Notes <span className="comment-tab-count">{activeComments.filter(c => c.is_internal).length}</span>
              </button>
              {showAtlasTab && (
                <button
                  className={`comment-tab comment-tab-atlas ${commentTab === 'atlas' ? 'active' : ''}`}
                  onClick={() => setCommentTab('atlas')}
                >
                  Atlas <span className="comment-tab-hex">&#x2B21;</span>
                </button>
              )}
              <button
                className={`comment-tab ${commentTab === 'all' ? 'active' : ''}`}
                onClick={() => setCommentTab('all')}
              >
                All <span className="comment-tab-count">{activeComments.length}</span>
              </button>
              <button
                className={`comment-tab ${commentTab === 'timeline' ? 'active' : ''}`}
                onClick={() => setCommentTab('timeline')}
              >
                Timeline
              </button>
            </div>

            {commentTab === 'atlas' ? (
              <AtlasTab
                ticketId={ticket.id}
                ticketSubject={ticket.subject}
                ticketDescription={ticket.description || ''}
                isDevItem={isDevItem}
              />
            ) : commentTab === 'timeline' ? (
              <div className="timeline-list">
                {activityLoading && (
                  <div className="comment-empty">Loading timeline...</div>
                )}
                {!activityLoading && activityLog.length === 0 && (
                  <div className="comment-empty">No activity recorded yet</div>
                )}
                {!activityLoading && activityLog.map((entry) => {
                  const icon = {
                    created: '\u2795',
                    status_changed: '\u{1F504}',
                    priority_changed: '\u{1F4CA}',
                    assigned: '\u{1F464}',
                    team_assigned: '\u{1F465}',
                    category_changed: '\u{1F4C1}',
                    comment_added: '\u{1F4AC}',
                  }[entry.activity_type] || '\u{1F4CB}';

                  let description = '';
                  switch (entry.activity_type) {
                    case 'created':
                      description = 'Ticket created';
                      break;
                    case 'status_changed':
                      description = `Status changed from ${entry.old_value || '?'} to ${entry.new_value || '?'}`;
                      break;
                    case 'priority_changed':
                      description = `Priority changed from ${entry.old_value || '?'} to ${entry.new_value || '?'}`;
                      break;
                    case 'assigned':
                      if (entry.old_value && entry.new_value) {
                        description = `Reassigned from ${entry.old_value} to ${entry.new_value}`;
                      } else if (entry.new_value) {
                        description = `Assigned to ${entry.new_value}`;
                      } else {
                        description = 'Assignee removed';
                      }
                      break;
                    case 'team_assigned':
                      if (entry.old_value && entry.new_value) {
                        description = `Team changed from ${entry.old_value} to ${entry.new_value}`;
                      } else if (entry.new_value) {
                        description = `Team set to ${entry.new_value}`;
                      } else {
                        description = 'Team removed';
                      }
                      break;
                    case 'category_changed':
                      if (entry.old_value && entry.new_value) {
                        description = `Category changed from ${entry.old_value} to ${entry.new_value}`;
                      } else if (entry.new_value) {
                        description = `Category set to ${entry.new_value}`;
                      } else {
                        description = 'Category removed';
                      }
                      break;
                    case 'comment_added':
                      description = entry.new_value === 'internal note' ? 'Added an internal note' : 'Added a reply';
                      break;
                    default:
                      description = entry.activity_type.replace(/_/g, ' ');
                  }

                  return (
                    <div key={entry.id} className="timeline-entry">
                      <span className="timeline-icon">{icon}</span>
                      <div className="timeline-content">
                        <span className="timeline-desc">
                          {entry.user_name && <strong>{entry.user_name}</strong>}
                          {entry.user_name ? ' ' : ''}{description}
                        </span>
                        <span className="timeline-time">{timeAgo(entry.created_at)}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <>
                <div className="comment-list">
                  {filteredComments.length === 0 && (
                    <div className="comment-empty">No comments yet</div>
                  )}
                  {filteredComments.map((c) => {
                    const isAtlas = c.is_ai_generated && !c.author_name;
                    const authorDisplay = isAtlas ? 'Atlas' : (c.author_name || 'Unknown');
                    return (
                    <div key={c.id} className={`comment-item ${c.is_internal ? 'comment-internal' : ''} ${isAtlas ? 'comment-atlas' : ''}`}>
                      <div className="comment-header">
                        <span className="comment-author">{authorDisplay}</span>
                        <div className="comment-header-right">
                          {c.is_internal && <span className="badge badge-warning-subtle">Internal</span>}
                          {isAtlas && <span className="badge badge-atlas">Atlas</span>}
                          <span className="comment-time">{timeAgo(c.created_at)}</span>
                        </div>
                      </div>
                      <div className="comment-body chat-markdown" dangerouslySetInnerHTML={{ __html: renderMarkdown(c.content || '') }} />
                    </div>
                    );
                  })}
                </div>

                {/* Reply box — pinned to bottom */}
                <form onSubmit={handleAddComment} className="comment-form">
                  <div className="reply-mode-toggle">
                    <button
                      type="button"
                      className={`reply-mode-btn ${replyMode === 'note' ? 'active note' : ''}`}
                      onClick={() => setReplyMode('note')}
                    >
                      Internal Note
                    </button>
                    <button
                      type="button"
                      className={`reply-mode-btn ${replyMode === 'reply' ? 'active reply' : ''}`}
                      onClick={() => setReplyMode('reply')}
                    >
                      Reply
                    </button>
                  </div>
                  <ReplyToolbar
                    textareaRef={commentRef}
                    setText={setComment}
                    getCurrentText={() => comment}
                    hint="Ctrl+Enter to send"
                  />
                  <textarea
                    ref={commentRef}
                    className="form-input form-textarea has-toolbar"
                    value={comment}
                    onChange={(e) => setComment(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); handleAddComment(e as unknown as React.FormEvent); } }}
                    placeholder={replyMode === 'note' ? 'Write an internal note (invisible to requester)...' : 'Write a reply to the requester...'}
                    rows={3}
                  />
                  <div className="comment-form-actions">
                    <div className="comment-form-options">
                      {replyMode === 'reply' && (
                        <>
                          <label className="comment-email-toggle">
                            <input
                              type="checkbox"
                              checked={sendEmail}
                              onChange={(e) => setSendEmail(e.target.checked)}
                            />
                            <span>Email requester</span>
                          </label>
                          <input
                            type="text"
                            className="form-input form-input-sm cc-input"
                            value={ccInput}
                            onChange={(e) => setCcInput(e.target.value)}
                            placeholder="CC: emails, comma-separated"
                          />
                        </>
                      )}
                    </div>
                    <button
                      type="submit"
                      className={`btn btn-sm ${replyMode === 'note' ? 'btn-warning' : 'btn-primary'}`}
                      disabled={submittingComment || !comment.trim()}
                    >
                      {submittingComment ? 'Posting...' : replyMode === 'note' ? 'Add Note' : 'Send Reply'}
                    </button>
                  </div>
                </form>
              </>
            )}
          </div>
        </div>

        {/* RIGHT: Properties sidebar */}
        <div className="ticket-workspace-sidebar">
          {/* Priority */}
          <div className="sidebar-field">
            <label className="sidebar-field-label">Priority</label>
            <select
              className="form-input form-select"
              value={ticket.priority}
              onChange={(e) => handlePriorityChange(e.target.value as TicketPriority)}
            >
              {PRIORITY_OPTIONS.map((p) => (
                <option key={p.value} value={p.value}>{p.label}</option>
              ))}
            </select>
          </div>

          {/* Assignee */}
          <div className="sidebar-field">
            <label className="sidebar-field-label">Assignee</label>
            <select
              className="form-input form-select"
              value={ticket.assignee_id || ''}
              onChange={(e) => handleAssigneeChange(e.target.value)}
            >
              <option value="">Unassigned</option>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </select>
          </div>

          {/* Team */}
          {teams.length > 0 && (
            <div className="sidebar-field">
              <label className="sidebar-field-label">Team</label>
              <select
                className="form-input form-select"
                value={(ticket as any).team_id || ''}
                onChange={(e) => handleTeamChange(e.target.value)}
              >
                <option value="">No Team</option>
                {teams.map((t) => (
                  <option key={t.id} value={t.id}>{t.name}</option>
                ))}
              </select>
            </div>
          )}

          {/* ===== DEV ITEM SIDEBAR ===== */}
          {isDevItem && (
            <>
              {/* Work Item Type */}
              <div className="sidebar-field">
                <label className="sidebar-field-label">Work Item Type</label>
                <select
                  className="form-input form-select"
                  value={(ticket as any).work_item_type_id || ''}
                  onChange={async (e) => {
                    const val = e.target.value ? parseInt(e.target.value) : null;
                    await updateTicket(ticket.id, { work_item_type_id: val } as any);
                  }}
                >
                  <option value="">None</option>
                  {workItemTypes.map((wt) => (
                    <option key={wt.id} value={wt.id}>{wt.icon ? wt.icon + ' ' : ''}{wt.name}</option>
                  ))}
                </select>
              </div>

              {/* Story Points */}
              <div className="sidebar-field">
                <label className="sidebar-field-label">Story Points</label>
                <input
                  className="form-input"
                  type="number"
                  min="0"
                  style={{ width: 80 }}
                  value={(ticket as any).story_points ?? ''}
                  onChange={async (e) => {
                    const val = e.target.value ? parseInt(e.target.value) : null;
                    await updateTicket(ticket.id, { story_points: val } as any);
                  }}
                />
              </div>

              {/* Sprint */}
              <div className="sidebar-field">
                <label className="sidebar-field-label">Sprint</label>
                <select
                  className="form-input form-select"
                  value={(ticket as any).sprint_id || ''}
                  onChange={async (e) => {
                    const val = e.target.value ? parseInt(e.target.value) : null;
                    await updateTicket(ticket.id, { sprint_id: val } as any);
                  }}
                >
                  <option value="">No Sprint</option>
                  {sprints.filter(s => s.status !== 'completed').map((s) => (
                    <option key={s.id} value={s.id}>{s.name}</option>
                  ))}
                </select>
              </div>

              <div className="sidebar-divider" />

              {/* Parent (Hierarchy) */}
              <div className="sidebar-field">
                <label className="sidebar-field-label">Parent</label>
                {parentItem ? (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
                    <button
                      className="incident-link"
                      style={{ fontSize: 12 }}
                      onClick={() => useUIStore.getState().openTicketDetail(parentItem.id)}
                    >
                      {parentItem.work_item_number || parentItem.ticket_number} — {parentItem.subject}
                    </button>
                    <button className="btn btn-ghost btn-xs" onClick={() => handleSetParent(null)}>x</button>
                  </div>
                ) : (
                  <button className="btn btn-ghost btn-xs" onClick={() => setShowParentSearch(true)}>
                    + Set Parent
                  </button>
                )}
                {showParentSearch && (
                  <div style={{ marginTop: 6 }}>
                    <input
                      className="form-input"
                      placeholder="Search work items..."
                      value={parentSearch}
                      onChange={(e) => handleParentSearch(e.target.value)}
                      autoFocus
                      style={{ marginBottom: 4 }}
                    />
                    <div style={{ maxHeight: 150, overflowY: 'auto', border: '1px solid var(--t-border)', borderRadius: 4 }}>
                      {parentResults.map((r: any) => (
                        <div
                          key={r.id}
                          style={{ padding: '4px 8px', cursor: 'pointer', fontSize: 11, borderBottom: '1px solid var(--t-border)' }}
                          onClick={() => handleSetParent(r.id)}
                        >
                          <span style={{ color: 'var(--t-text-dim)' }}>{r.work_item_number || r.ticket_number}</span>{' '}
                          <span style={{ color: 'var(--t-text-bright)' }}>{r.subject}</span>
                        </div>
                      ))}
                    </div>
                    <button className="btn btn-ghost btn-xs" onClick={() => { setShowParentSearch(false); setParentSearch(''); setParentResults([]); }} style={{ marginTop: 4 }}>Cancel</button>
                  </div>
                )}
              </div>

              {/* Children / Rollup */}
              {children.length > 0 && (
                <div className="sidebar-field">
                  <label className="sidebar-field-label">
                    Children ({children.length})
                    {rollup && rollup.total_points > 0 && (
                      <span style={{ fontWeight: 400, color: 'var(--t-text-dim)', marginLeft: 6 }}>
                        {rollup.completion_pct}% complete
                      </span>
                    )}
                  </label>
                  {rollup && rollup.total_points > 0 && (
                    <div style={{ height: 4, background: 'var(--t-border)', borderRadius: 2, marginBottom: 6, overflow: 'hidden' }}>
                      <div style={{ height: '100%', width: `${rollup.completion_pct}%`, background: 'var(--c-success)', borderRadius: 2 }} />
                    </div>
                  )}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                    {children.slice(0, 10).map((c: any) => (
                      <div
                        key={c.id}
                        style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, cursor: 'pointer', padding: '2px 0' }}
                        onClick={() => useUIStore.getState().openTicketDetail(c.id)}
                      >
                        {c.work_item_type_icon && <span style={{ fontSize: 10 }}>{c.work_item_type_icon}</span>}
                        <span style={{ color: 'var(--t-text-dim)' }}>{c.work_item_number || c.ticket_number}</span>
                        <span style={{ color: 'var(--t-text-bright)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.subject}</span>
                        <span style={{ fontSize: 9, color: 'var(--t-text-muted)' }}>{c.status?.replace(/_/g, ' ')}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div className="sidebar-divider" />

              {/* Acceptance Criteria */}
              <div className="sidebar-field">
                <label className="sidebar-field-label" style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span>Acceptance Criteria</span>
                  {!editingAC && (
                    <button
                      className="btn btn-ghost btn-xs"
                      style={{ fontSize: 9 }}
                      onClick={() => { setEditingAC(true); setAcDraft((ticket as any).acceptance_criteria || ''); }}
                    >Edit</button>
                  )}
                </label>
                {editingAC ? (
                  <div>
                    <textarea
                      className="form-input form-textarea"
                      value={acDraft}
                      onChange={(e) => setAcDraft(e.target.value)}
                      rows={4}
                      placeholder="Define what 'done' looks like..."
                      style={{ fontSize: 12 }}
                    />
                    <div style={{ display: 'flex', gap: 4, marginTop: 4 }}>
                      <button className="btn btn-primary btn-xs" onClick={async () => {
                        await updateTicket(ticket.id, { acceptance_criteria: acDraft } as any);
                        setEditingAC(false);
                      }}>Save</button>
                      <button className="btn btn-ghost btn-xs" onClick={() => setEditingAC(false)}>Cancel</button>
                    </div>
                  </div>
                ) : (ticket as any).acceptance_criteria ? (
                  <div style={{ fontSize: 12, color: 'var(--t-text-muted)', whiteSpace: 'pre-wrap' }}>
                    {(ticket as any).acceptance_criteria}
                  </div>
                ) : (
                  <div style={{ fontSize: 11, color: 'var(--t-text-dim)', fontStyle: 'italic' }}>Not defined</div>
                )}
              </div>

              {/* Bug Built-in Fields */}
              {(ticket as any).ticket_type === 'bug' && (
                <div className="sidebar-field" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {(ticket as any).steps_to_reproduce && (
                    <div>
                      <label className="sidebar-field-label">Steps to Reproduce</label>
                      <div style={{ fontSize: 12, color: 'var(--t-text-muted)', whiteSpace: 'pre-wrap' }}>
                        {(ticket as any).steps_to_reproduce}
                      </div>
                    </div>
                  )}
                  {(ticket as any).expected_behavior && (
                    <div>
                      <label className="sidebar-field-label">Expected Behavior</label>
                      <div style={{ fontSize: 12, color: 'var(--t-text-muted)', whiteSpace: 'pre-wrap' }}>
                        {(ticket as any).expected_behavior}
                      </div>
                    </div>
                  )}
                  {(ticket as any).actual_behavior && (
                    <div>
                      <label className="sidebar-field-label">Actual Behavior</label>
                      <div style={{ fontSize: 12, color: 'var(--t-text-muted)', whiteSpace: 'pre-wrap' }}>
                        {(ticket as any).actual_behavior}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Subtask Checklist */}
              <div className="sidebar-field">
                <label className="sidebar-field-label">
                  Checklist {subtasks.length > 0 && `(${subtasks.filter(s => s.status === 'done').length}/${subtasks.length})`}
                </label>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 2, marginBottom: 6 }}>
                  {subtasks.map((st) => (
                    <div key={st.id} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
                      <input
                        type="checkbox"
                        checked={st.status === 'done'}
                        onChange={async () => {
                          const newStatus = st.status === 'done' ? 'todo' : 'done';
                          await api.updateTicketTask(ticket.id, st.id, { status: newStatus });
                          setSubtasks(await api.listTicketTasks(ticket.id));
                        }}
                      />
                      <span style={{ flex: 1, color: st.status === 'done' ? 'var(--t-text-dim)' : 'var(--t-text-bright)', textDecoration: st.status === 'done' ? 'line-through' : 'none' }}>
                        {st.title}
                      </span>
                      <button className="btn btn-ghost btn-xs" style={{ fontSize: 9, color: 'var(--t-text-dim)' }}
                        onClick={async () => { await api.deleteTicketTask(ticket.id, st.id); setSubtasks(await api.listTicketTasks(ticket.id)); }}
                      >x</button>
                    </div>
                  ))}
                </div>
                <div style={{ display: 'flex', gap: 4 }}>
                  <input
                    className="form-input"
                    value={newSubtask}
                    onChange={(e) => setNewSubtask(e.target.value)}
                    placeholder="Add checklist item..."
                    style={{ flex: 1, height: 26, fontSize: 11 }}
                    onKeyDown={async (e) => {
                      if (e.key === 'Enter' && newSubtask.trim()) {
                        await api.createTicketTask(ticket.id, { title: newSubtask.trim() });
                        setNewSubtask('');
                        setSubtasks(await api.listTicketTasks(ticket.id));
                      }
                    }}
                  />
                </div>
              </div>

              <div className="sidebar-divider" />
            </>
          )}

          {/* ===== SUPPORT ITEM SIDEBAR ===== */}
          {!isDevItem && (
            <>
              {/* Requester */}
              <div className="sidebar-field">
                <label className="sidebar-field-label">Requester</label>
                <select
                  className="form-input form-select"
                  value={ticket.requester_id || ''}
                  onChange={(e) => handleRequesterChange(e.target.value)}
                >
                  <option value="">Unknown</option>
                  {allUsers.map((u) => (
                    <option key={u.id} value={u.id}>{u.name}{u.email ? ` (${u.email})` : ''}</option>
                  ))}
                </select>
              </div>

              <div className="sidebar-divider" />

              {/* SLA Status */}
              <div className="sidebar-field">
                <label className="sidebar-field-label">SLA Status</label>
                <div className="sla-badge-row">
                  <span className={`sla-badge sla-${ticket.sla_status}`}>
                    {slaStatusLabel(ticket.sla_status)}
                  </span>
                  {ticket.sla_due_at && (
                    <span className="sla-remaining" style={{ color: slaColor }}>
                      {formatSlaRemaining(ticket)}
                    </span>
                  )}
                </div>
              </div>

              {/* First Response */}
              {ticket.sla_first_response_due && (
                <div className="sidebar-field">
                  <label className="sidebar-field-label">First Response</label>
                  <div className="sidebar-field-value">
                    {ticket.first_response_at ? (
                      <span style={{ color: 'var(--t-success)' }}>{timeAgo(ticket.first_response_at)}</span>
                    ) : (
                      <span style={{ color: 'var(--t-warning)' }}>Awaiting response</span>
                    )}
                  </div>
                </div>
              )}

              <div className="sidebar-divider" />
            </>
          )}

          {/* Timestamps (shared) */}
          <div className="sidebar-field">
            <label className="sidebar-field-label">Created</label>
            <div className="sidebar-field-value sidebar-field-mono">{new Date(ticket.created_at).toLocaleString()}</div>
          </div>
          <div className="sidebar-field">
            <label className="sidebar-field-label">Updated</label>
            <div className="sidebar-field-value sidebar-field-mono">{new Date(ticket.updated_at).toLocaleString()}</div>
          </div>
          {(ticket as any).source && (
            <div className="sidebar-field">
              <label className="sidebar-field-label">Source</label>
              <div className="sidebar-field-value">
                {{
                  phone:  'Phone Call',
                  email:  'Email',
                  portal: 'Customer Portal',
                  web:    'Web Portal',
                }[(ticket as any).source as string] ?? (ticket as any).source}
              </div>
            </div>
          )}

          {/* Location & Category (support only) */}
          {!isDevItem && (
            <>
              <div className="sidebar-divider" />

              <div className="sidebar-field">
                <label className="sidebar-field-label">Location</label>
                <CascadingSelect
                  items={locations}
                  value={ticket.location_id}
                  onChange={handleLocationChange}
                  placeholder="Select location..."
                />
              </div>

              <div className="sidebar-field">
                <label className="sidebar-field-label">{problemFieldLabel}</label>
                <CascadingSelect
                  items={problemCategories}
                  value={ticket.problem_category_id}
                  onChange={handleProblemCategoryChange}
                  placeholder={`Select ${problemFieldLabel.toLowerCase()}...`}
                />
              </div>
            </>
          )}

          <div className="sidebar-divider" />

          {/* Tags (shared) */}
          <div className="sidebar-field">
            <label className="sidebar-field-label">Tags</label>
            <TagSuggestions
              ticketId={ticket.id}
              tags={ticket.tags || []}
              suggestions={tagSuggestions}
              onUpdate={() => loadTicket(ticket.id)}
            />
          </div>

          {/* Custom Fields */}
          {customFields.length > 0 && (
            <>
              <div className="sidebar-divider" />
              <CustomFieldsPanel
                fields={customFields}
                ticketId={ticket.id}
                onUpdated={() => loadCustomFields(ticket.id)}
                highlightFields={highlightFields}
              />
            </>
          )}

          {/* Suggested KB Articles — support only */}
          {!isDevItem && suggestedArticles.length > 0 && (
            <>
              <div className="sidebar-divider" />
              <div className="sidebar-field">
                <div
                  className="sidebar-field-label"
                  style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}
                  onClick={() => setArticlesCollapsed(!articlesCollapsed)}
                >
                  <span>Suggested Articles</span>
                  <span style={{ fontSize: 10, color: 'var(--t-text-muted)', transition: 'transform 0.2s', transform: articlesCollapsed ? 'rotate(0deg)' : 'rotate(90deg)' }}>&#9656;</span>
                </div>
                {!articlesCollapsed && (
                  <div className="suggested-articles">
                    {suggestedArticles.map((a) => (
                      <div key={a.id} className="suggested-article">
                        <div className="suggested-article-top">
                          <span className="badge badge-medium" style={{ fontSize: 9, padding: '0 5px' }}>{a.module_name}</span>
                          <span className="suggested-article-title">{a.title}</span>
                        </div>
                        {a.tags && a.tags.length > 0 && (
                          <div className="suggested-article-tags">
                            {a.tags.slice(0, 3).map((t: string) => (
                              <span key={t} className="tag-chip-doc">{t}</span>
                            ))}
                          </div>
                        )}
                        <button
                          className={`btn btn-xs ${sentArticles.has(a.id) ? 'btn-ghost' : 'btn-primary'}`}
                          disabled={sendingArticle === a.id || sentArticles.has(a.id)}
                          onClick={async () => {
                            setSendingArticle(a.id);
                            try {
                              await api.sendArticleToTicket(a.id, ticket.id);
                              setSentArticles((prev) => new Set(prev).add(a.id));
                              loadTicket(ticket.id);
                            } catch {}
                            setSendingArticle(null);
                          }}
                        >
                          {sentArticles.has(a.id) ? 'Sent' : sendingArticle === a.id ? 'Sending...' : 'Send As Response'}
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
