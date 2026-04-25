import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import { useUIStore } from '../../store/uiStore';
import type { AuditQueueItem, AuditStats, KnowledgeGap, MetricsSummary } from '../../types';

type QueueFilter = '' | 'auto_resolved' | 'human_resolved' | 'low_confidence' | 'kba_candidate';
type QueueView = 'pending' | 'reviewed';
type Tab = 'queue' | 'gaps' | 'metrics' | 'settings';

const TIER_CONFIG: Record<string, { label: string; color: string; bg: string; tooltip: string }> = {
  auto_resolved:  { label: 'Auto',     color: '#60a5fa', bg: 'rgba(96,165,250,0.15)',  tooltip: 'Resolved by Atlas AI without human intervention' },
  human_resolved: { label: 'Human',    color: '#34d399', bg: 'rgba(52,211,153,0.15)',  tooltip: 'Resolved by a human agent' },
  low_confidence: { label: 'Low Conf', color: '#fbbf24', bg: 'rgba(251,191,36,0.15)',  tooltip: 'Atlas had low confidence in its response — needs human review' },
  kba_candidate:  { label: 'KBA',      color: '#a78bfa', bg: 'rgba(167,139,250,0.15)', tooltip: 'Good candidate for a Knowledge Base Article' },
};

function QualityBar({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const color = pct < 40 ? '#ef4444' : pct < 70 ? '#fbbf24' : '#34d399';
  const label = pct < 40 ? 'Poor' : pct < 70 ? 'Fair' : 'Good';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }} title={`Resolution Quality: ${pct}% — ${label}. Scored by AI based on whether the root cause was identified, troubleshooting steps provided, and issue actually resolved.`}>
      <div style={{ width: 60, height: 6, borderRadius: 3, background: 'var(--surface-3, rgba(255,255,255,0.1))' }}>
        <div style={{ width: `${pct}%`, height: '100%', borderRadius: 3, background: color, transition: 'width 0.3s' }} />
      </div>
      <span style={{ fontSize: 11, color, fontWeight: 600 }}>{pct}%</span>
    </div>
  );
}

function TierBadge({ type }: { type: string }) {
  const cfg = TIER_CONFIG[type] || { label: type, color: '#999', bg: 'rgba(150,150,150,0.15)', tooltip: type };
  return (
    <span title={cfg.tooltip} style={{
      padding: '2px 8px', borderRadius: 10, fontSize: 11, fontWeight: 600,
      color: cfg.color, background: cfg.bg, whiteSpace: 'nowrap', cursor: 'help',
    }}>
      {cfg.label}
    </span>
  );
}

export function AuditQueue() {
  const openTicketDetail = useUIStore((s) => s.openTicketDetail);
  const setView = useUIStore((s) => s.setView);
  const [tab, setTab] = useState<Tab>('queue');
  const [items, setItems] = useState<AuditQueueItem[]>([]);
  const [stats, setStats] = useState<AuditStats | null>(null);
  const [gaps, setGaps] = useState<KnowledgeGap[]>([]);
  const [metrics, setMetrics] = useState<MetricsSummary | null>(null);
  const [metricsLoaded, setMetricsLoaded] = useState(false);
  const [filter, setFilter] = useState<QueueFilter>('');
  const [queueView, setQueueView] = useState<QueueView>('pending');
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [autoCloseDays, setAutoCloseDays] = useState(7);
  const [autoApproveThreshold, setAutoApproveThreshold] = useState(80);
  const [autoDismissThreshold, setAutoDismissThreshold] = useState(0);
  const [settingsSaved, setSettingsSaved] = useState(false);
  const [confirmAction, setConfirmAction] = useState<string | null>(null);

  const loadQueue = async () => {
    setLoading(true);
    try {
      const params: Record<string, string> = { status: queueView === 'pending' ? 'pending' : 'approved,dismissed,auto_closed,auto_approved,auto_dismissed' };
      if (filter) params.queue_type = filter;
      const [res, s] = await Promise.all([api.listAuditQueue(params), api.getAuditStats()]);
      setItems(res.items);
      setStats(s);
    } catch {}
    setLoading(false);
  };

  const loadGaps = async () => {
    try { setGaps(await api.listKnowledgeGaps('detected')); } catch {}
  };

  const loadMetrics = async () => {
    try {
      const m = await api.getMetricsSummary();
      setMetrics(m);
    } catch {}
    setMetricsLoaded(true);
  };

  const loadSettings = async () => {
    try {
      const s = await api.getAuditSettings();
      setAutoCloseDays(s.ai_audit_auto_close_days || 7);
      setAutoApproveThreshold(s.ai_audit_auto_approve_threshold ?? 80);
      setAutoDismissThreshold(s.ai_audit_auto_dismiss_threshold ?? 0);
    } catch {}
  };

  useEffect(() => { loadQueue(); loadGaps(); loadMetrics(); loadSettings(); }, []);
  useEffect(() => { loadQueue(); }, [filter, queueView]);

  const handleReview = async (itemId: number, action: 'approve' | 'dismiss') => {
    try {
      await api.reviewAuditItem(itemId, action);
      setItems((prev) => prev.filter((i) => i.id !== itemId));
      setSelected((prev) => { const next = new Set(prev); next.delete(itemId); return next; });
      const s = await api.getAuditStats();
      setStats(s);
    } catch {}
  };

  const handleReopen = async (itemId: number) => {
    try {
      await api.reopenAuditItem(itemId);
      loadQueue();
    } catch {}
  };

  const handleBulk = async (action: 'approve' | 'dismiss') => {
    if (selected.size === 0) return;
    try {
      await api.bulkManageQueue(action, Array.from(selected));
      loadQueue();
      setSelected(new Set());
    } catch {}
  };

  const handleCloseAll = async () => {
    if (confirmAction !== 'close-all-confirm') {
      setConfirmAction('close-all');
      return;
    }
    try {
      await api.bulkManageQueue('approve', undefined, true);
      loadQueue();
      setSelected(new Set());
    } catch {}
    setConfirmAction(null);
  };

  const handleSaveSettings = async () => {
    try {
      await api.updateAuditSettings({
        ai_audit_auto_close_days: autoCloseDays,
        ai_audit_auto_approve_threshold: autoApproveThreshold,
        ai_audit_auto_dismiss_threshold: autoDismissThreshold,
      });
      setSettingsSaved(true);
      setTimeout(() => setSettingsSaved(false), 3000);
    } catch {}
  };

  const handleDetectGaps = async () => {
    await api.detectKnowledgeGaps();
    setTimeout(loadGaps, 3000);
  };

  const toggleSelect = (id: number) => {
    setSelected((prev) => { const next = new Set(prev); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  };

  const goToTicket = (ticketId: number) => { setView('tickets'); openTicketDetail(ticketId); };

  return (
    <div className="audit-queue">
      {/* Confirmation modal */}
      {confirmAction && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 9999 }}
          onClick={() => setConfirmAction(null)}>
          <div style={{ background: 'var(--t-panel)', border: '1px solid var(--t-border)', borderRadius: 12, padding: 32, maxWidth: 420, textAlign: 'center' }}
            onClick={(e) => e.stopPropagation()}>
            {confirmAction === 'close-all' && (
              <>
                <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--t-text-bright)', marginBottom: 12 }}>
                  Close All Pending Audit Items?
                </div>
                <div style={{ fontSize: 13, color: 'var(--t-text-muted)', marginBottom: 24 }}>
                  This will approve all {stats?.total_pending || 0} pending items. This action cannot be easily undone.
                </div>
                <div style={{ display: 'flex', gap: 12, justifyContent: 'center' }}>
                  <button className="btn btn-ghost" onClick={() => setConfirmAction(null)}>Cancel</button>
                  <button className="btn btn-warning" onClick={() => setConfirmAction('close-all-confirm')}>
                    Yes, Close All
                  </button>
                </div>
              </>
            )}
            {confirmAction === 'close-all-confirm' && (
              <>
                <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--t-warning)', marginBottom: 12 }}>
                  Are you absolutely sure?
                </div>
                <div style={{ fontSize: 13, color: 'var(--t-text-muted)', marginBottom: 24 }}>
                  All {stats?.total_pending || 0} pending audit items will be marked as approved. You can reopen them later from the Reviewed tab.
                </div>
                <div style={{ display: 'flex', gap: 12, justifyContent: 'center' }}>
                  <button className="btn btn-ghost" onClick={() => setConfirmAction(null)}>Cancel</button>
                  <button className="btn btn-primary" onClick={handleCloseAll}>
                    Confirm — Approve All
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* Stats */}
      {stats && (
        <div className="audit-stats">
          <div className="audit-stat" title="Total items awaiting review">
            <span className="audit-stat-value">{stats.total_pending}</span>
            <span className="audit-stat-label">Pending</span>
          </div>
          <div className="audit-stat" title="Tickets with potential for a Knowledge Base Article">
            <span className="audit-stat-value">{stats.kba_candidates}</span>
            <span className="audit-stat-label">KBA Candidates</span>
          </div>
          <div className="audit-stat" title="Atlas had low confidence — these need human review most urgently">
            <span className="audit-stat-value">{stats.low_confidence}</span>
            <span className="audit-stat-label">Low Confidence</span>
          </div>
          {stats.avg_resolution_score != null && (
            <div className="audit-stat" title="Average AI-scored resolution quality across all audited tickets (0-100%). Based on root cause identification, troubleshooting steps, and actual resolution.">
              <span className="audit-stat-value">{(stats.avg_resolution_score * 100).toFixed(0)}%</span>
              <span className="audit-stat-label">Avg Quality</span>
            </div>
          )}
          {metrics?.fcr_rate != null && (
            <div className="audit-stat" title="First Contact Resolution — % of tickets resolved without escalation or reassignment">
              <span className="audit-stat-value">{metrics.fcr_rate}%</span>
              <span className="audit-stat-label">FCR Rate</span>
            </div>
          )}
          {metrics?.avg_effort_score != null && (
            <div className="audit-stat" title="Customer effort score (1=easy, 5=difficult). Based on reply count, escalations, and resolution time.">
              <span className="audit-stat-value">{Number(metrics.avg_effort_score).toFixed(1)}</span>
              <span className="audit-stat-label">Avg Effort</span>
            </div>
          )}
        </div>
      )}

      {/* Tabs */}
      <div className="comment-tabs">
        <button className={`comment-tab ${tab === 'queue' ? 'active' : ''}`} onClick={() => setTab('queue')}>
          Audit Queue {stats?.total_pending ? <span className="comment-tab-count">{stats.total_pending}</span> : null}
        </button>
        <button className={`comment-tab ${tab === 'gaps' ? 'active' : ''}`} onClick={() => setTab('gaps')}>
          Knowledge Gaps {gaps.length > 0 && <span className="comment-tab-count">{gaps.length}</span>}
        </button>
        <button className={`comment-tab ${tab === 'metrics' ? 'active' : ''}`} onClick={() => setTab('metrics')}>Metrics</button>
        <button className={`comment-tab ${tab === 'settings' ? 'active' : ''}`} onClick={() => setTab('settings')}>Settings</button>
      </div>

      {tab === 'queue' && (
        <>
          {/* Controls */}
          <div className="audit-controls">
            {/* Pending / Reviewed toggle */}
            <div className="audit-type-filter">
              <button className={`audit-type-btn ${queueView === 'pending' ? 'active' : ''}`}
                onClick={() => { setQueueView('pending'); setFilter(''); }}
                style={queueView === 'pending' ? { background: 'rgba(52,211,153,0.15)', borderColor: '#34d399', color: '#34d399' } : {}}>
                Pending
              </button>
              <button className={`audit-type-btn ${queueView === 'reviewed' ? 'active' : ''}`}
                onClick={() => { setQueueView('reviewed'); setFilter(''); }}
                style={queueView === 'reviewed' ? { background: 'rgba(150,150,150,0.15)', borderColor: '#999', color: '#999' } : {}}>
                Reviewed
              </button>
            </div>

            <div style={{ width: 1, height: 20, background: 'var(--t-border)', margin: '0 4px' }} />

            {/* Type filters */}
            <div className="audit-type-filter">
              <button className={`audit-type-btn ${filter === '' ? 'active' : ''}`} onClick={() => setFilter('')}
                style={filter === '' ? { background: 'rgba(255,255,255,0.05)', borderColor: 'var(--t-border-light)', color: 'var(--t-text)' } : {}}>
                All
              </button>
              {(['auto_resolved', 'human_resolved', 'low_confidence', 'kba_candidate'] as const).map((val) => {
                const cfg = TIER_CONFIG[val];
                return (
                  <button key={val} className={`audit-type-btn ${filter === val ? 'active' : ''}`}
                    onClick={() => setFilter(val)} title={cfg.tooltip}
                    style={filter === val ? { background: cfg.bg, borderColor: cfg.color, color: cfg.color } : {}}>
                    <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: cfg.color }} />
                    {cfg.label}
                  </button>
                );
              })}
            </div>

            {queueView === 'pending' && (
              <>
                {selected.size > 0 && (
                  <>
                    <button className="btn btn-xs btn-primary" onClick={() => handleBulk('approve')}>Approve {selected.size}</button>
                    <button className="btn btn-xs btn-ghost" onClick={() => handleBulk('dismiss')}>Dismiss {selected.size}</button>
                  </>
                )}
                {items.length > 0 && (
                  <button className="btn btn-xs btn-ghost" onClick={() => setConfirmAction('close-all')}>Close All Pending</button>
                )}
              </>
            )}
          </div>

          {/* Queue items */}
          <div className="audit-list">
            {loading && <div className="audit-empty">Loading...</div>}
            {!loading && items.length === 0 && (
              <div className="audit-empty">
                {queueView === 'pending' ? 'No pending items in the audit queue.' : 'No reviewed items found.'}
              </div>
            )}
            {items.map((item) => (
              <div key={item.id} className="audit-item" onClick={() => goToTicket(item.ticket_id)}>
                <div className="audit-item-main">
                  <div className="audit-item-header">
                    {queueView === 'pending' && (
                      <input type="checkbox" checked={selected.has(item.id)}
                        onChange={() => toggleSelect(item.id)} onClick={(e) => e.stopPropagation()} />
                    )}
                    <span className="audit-item-ticket">{item.ticket_number}</span>
                    <TierBadge type={item.queue_type} />
                    {item.resolution_score != null && <QualityBar score={item.resolution_score} />}
                    {queueView === 'reviewed' && (
                      <span style={{
                        fontSize: 10, padding: '2px 6px', borderRadius: 4, fontWeight: 600,
                        background: ['approved', 'auto_approved'].includes((item as any).status) ? 'rgba(52,211,153,0.15)'
                          : ['auto_closed'].includes((item as any).status) ? 'rgba(156,163,175,0.15)' : 'rgba(239,68,68,0.15)',
                        color: ['approved', 'auto_approved'].includes((item as any).status) ? '#34d399'
                          : ['auto_closed'].includes((item as any).status) ? '#9ca3af' : '#ef4444',
                      }}>
                        {(item as any).status === 'approved' ? 'Approved'
                          : (item as any).status === 'auto_approved' ? 'Auto-Approved'
                          : (item as any).status === 'auto_dismissed' ? 'Auto-Dismissed'
                          : (item as any).status === 'auto_closed' ? 'Auto-Closed'
                          : 'Dismissed'}
                      </span>
                    )}
                  </div>
                  <div className="audit-item-subject">{item.subject}</div>
                  <div className="audit-item-meta">
                    {item.suggested_category_name && item.suggested_category_name !== item.current_category_name && (
                      <span className="audit-item-score">
                        Category: {item.current_category_name || 'None'} &rarr; {item.suggested_category_name}
                        {item.ai_category_confidence != null && ` (${(item.ai_category_confidence * 100).toFixed(0)}%)`}
                      </span>
                    )}
                  </div>
                  {item.ai_suggested_tags.length > 0 && (
                    <div className="audit-item-tags">
                      {item.ai_suggested_tags.map((t) => <span key={t} className="audit-tag-chip">{t}</span>)}
                    </div>
                  )}
                  {item.resolution_notes && <div className="audit-item-notes">{item.resolution_notes}</div>}
                </div>
                <div className="audit-item-actions" onClick={(e) => e.stopPropagation()}>
                  {queueView === 'pending' ? (
                    <>
                      <button className="btn btn-xs btn-primary" onClick={() => handleReview(item.id, 'approve')}>Approve</button>
                      <button className="btn btn-xs btn-ghost" onClick={() => handleReview(item.id, 'dismiss')}>Dismiss</button>
                    </>
                  ) : (
                    <button className="btn btn-xs btn-ghost" onClick={() => handleReopen(item.id)}>Reopen</button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {tab === 'gaps' && (
        <div className="knowledge-gaps">
          <div className="audit-controls">
            <button className="btn btn-sm btn-primary" onClick={handleDetectGaps}>Run Gap Detection</button>
          </div>
          {gaps.length === 0 && <div className="audit-empty">No knowledge gaps detected. Run detection to scan recent tickets.</div>}
          {gaps.map((gap) => (
            <div key={gap.id} className="gap-item">
              <div className="gap-info">
                <div className="gap-topic">{gap.topic}</div>
                <div className="gap-meta">
                  <span>{gap.ticket_count} tickets</span>
                  {gap.suggested_title && <span>Suggested: {gap.suggested_title}</span>}
                </div>
              </div>
              <div className="gap-actions">
                <button className="btn btn-xs btn-primary" onClick={() => api.updateKnowledgeGap(gap.id, 'acknowledged').then(loadGaps)}>Acknowledge</button>
                <button className="btn btn-xs btn-ghost" onClick={() => api.updateKnowledgeGap(gap.id, 'dismissed').then(loadGaps)}>Dismiss</button>
              </div>
            </div>
          ))}
        </div>
      )}

      {tab === 'metrics' && (
        <div style={{ padding: '16px 0' }}>
          {!metricsLoaded ? (
            <div className="audit-empty">Loading metrics...</div>
          ) : metrics && Number(metrics.total_tickets) > 0 ? (
            <>
            <div className="audit-stats" style={{ justifyContent: 'center' }}>
              <div className="audit-stat" title="Total tickets with metrics tracked">
                <span className="audit-stat-value">{metrics.total_tickets}</span>
                <span className="audit-stat-label">Tickets Tracked</span>
              </div>
              <div className="audit-stat" title="% of tickets resolved on first contact without escalation">
                <span className="audit-stat-value">{metrics.fcr_rate != null ? `${Number(metrics.fcr_rate).toFixed(1)}%` : 'N/A'}</span>
                <span className="audit-stat-label">First Contact Resolution</span>
              </div>
              <div className="audit-stat" title="Average customer effort score (1=easy, 5=difficult)">
                <span className="audit-stat-value">{metrics.avg_effort_score != null ? Number(metrics.avg_effort_score).toFixed(1) : 'N/A'}</span>
                <span className="audit-stat-label">Avg Customer Effort</span>
              </div>
              <div className="audit-stat" title="Average number of replies before resolution">
                <span className="audit-stat-value">{metrics.avg_replies != null ? Number(metrics.avg_replies).toFixed(1) : 'N/A'}</span>
                <span className="audit-stat-label">Avg Replies / Ticket</span>
              </div>
              <div className="audit-stat" title="Average number of escalations per ticket">
                <span className="audit-stat-value">{metrics.avg_escalations != null ? Number(metrics.avg_escalations).toFixed(2) : 'N/A'}</span>
                <span className="audit-stat-label">Avg Escalations</span>
              </div>
            </div>

            {/* Article Effectiveness Section */}
            {((metrics as any).rated_articles > 0 || (metrics as any).top_articles?.length > 0) && (
              <div style={{ marginTop: 20, padding: '12px 14px', background: 'var(--t-panel-alt)', borderRadius: 'var(--radius-xs)', border: '1px solid var(--t-border)' }}>
                <h4 style={{ fontSize: 13, fontWeight: 600, color: 'var(--t-text-bright)', marginBottom: 10 }}>Article Effectiveness</h4>
                <div className="audit-stats" style={{ justifyContent: 'flex-start', marginBottom: 10 }}>
                  <div className="audit-stat" title="Average effectiveness of rated articles">
                    <span className="audit-stat-value">{(metrics as any).article_effectiveness_avg != null ? `${(Number((metrics as any).article_effectiveness_avg) * 100).toFixed(0)}%` : 'N/A'}</span>
                    <span className="audit-stat-label">Avg Effectiveness</span>
                  </div>
                  <div className="audit-stat" title="Articles with enough ratings for scoring">
                    <span className="audit-stat-value">{(metrics as any).rated_articles ?? 0}</span>
                    <span className="audit-stat-label">Rated Articles</span>
                  </div>
                  <div className="audit-stat" title="Articles with <30% positive ratings (5+ ratings)">
                    <span className="audit-stat-value" style={{ color: (metrics as any).low_effectiveness_count > 0 ? 'var(--t-error)' : undefined }}>{(metrics as any).low_effectiveness_count ?? 0}</span>
                    <span className="audit-stat-label">Need Review</span>
                  </div>
                </div>
                {(metrics as any).top_articles?.length > 0 && (
                  <div style={{ fontSize: 12 }}>
                    <div style={{ fontWeight: 600, color: 'var(--t-text)', marginBottom: 4 }}>Top Performers</div>
                    {(metrics as any).top_articles.map((a: any) => (
                      <div key={a.id} style={{ display: 'flex', justifyContent: 'space-between', padding: '2px 0', color: 'var(--t-text-muted)' }}>
                        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{a.title}</span>
                        <span style={{ color: 'var(--t-success)', marginLeft: 8, flexShrink: 0 }}>{(a.effectiveness_score * 100).toFixed(0)}% ({a.rating_count})</span>
                      </div>
                    ))}
                  </div>
                )}
                {(metrics as any).low_effectiveness_articles?.length > 0 && (metrics as any).low_effectiveness_articles.some((a: any) => a.effectiveness_score < 0.5) && (
                  <div style={{ fontSize: 12, marginTop: 8 }}>
                    <div style={{ fontWeight: 600, color: 'var(--t-error)', marginBottom: 4 }}>Low Performers</div>
                    {(metrics as any).low_effectiveness_articles.filter((a: any) => a.effectiveness_score < 0.5).map((a: any) => (
                      <div key={a.id} style={{ display: 'flex', justifyContent: 'space-between', padding: '2px 0', color: 'var(--t-text-muted)' }}>
                        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{a.title}</span>
                        <span style={{ color: 'var(--t-error)', marginLeft: 8, flexShrink: 0 }}>{(a.effectiveness_score * 100).toFixed(0)}% ({a.rating_count})</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
            </>
          ) : (
            <div className="audit-empty">No metrics data available yet. Metrics are generated when tickets are resolved or closed.</div>
          )}
        </div>
      )}

      {tab === 'settings' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 24, maxWidth: 500, padding: '8px 0' }}>
          <div>
            <h4 style={{ fontSize: 13, fontWeight: 600, color: 'var(--t-text-bright)', marginBottom: 12 }}>Auto-Close</h4>
            <div className="sidebar-field">
              <label className="sidebar-field-label">Auto-Close Unreviewed Items (Days)</label>
              <input type="number" className="form-input" value={autoCloseDays}
                onChange={(e) => setAutoCloseDays(parseInt(e.target.value) || 7)} min={1} max={90} style={{ width: 100 }} />
              <div style={{ fontSize: 11, color: 'var(--t-text-dim)', marginTop: 4 }}>
                Pending audit items will be automatically approved after this many days with no action.
              </div>
            </div>
          </div>
          <div>
            <h4 style={{ fontSize: 13, fontWeight: 600, color: 'var(--t-text-bright)', marginBottom: 12 }}>Auto-Approve Rule</h4>
            <div className="sidebar-field">
              <label className="sidebar-field-label">Quality Score Threshold (%)</label>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <input type="range" min={0} max={100} value={autoApproveThreshold}
                  onChange={(e) => setAutoApproveThreshold(Number(e.target.value))} style={{ flex: 1 }} />
                <span style={{ fontFamily: 'var(--mono)', fontSize: 13, fontWeight: 600, color: 'var(--t-text-bright)', minWidth: 40, textAlign: 'right' }}>{autoApproveThreshold}%</span>
              </div>
              <div style={{ fontSize: 11, color: 'var(--t-text-dim)', marginTop: 4 }}>
                Items with resolution quality above this threshold are auto-approved. Set to 0 to disable.
              </div>
            </div>
          </div>
          <div>
            <h4 style={{ fontSize: 13, fontWeight: 600, color: 'var(--t-text-bright)', marginBottom: 12 }}>Auto-Dismiss Rule</h4>
            <div className="sidebar-field">
              <label className="sidebar-field-label">Quality Score Floor (%)</label>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <input type="range" min={0} max={50} value={autoDismissThreshold}
                  onChange={(e) => setAutoDismissThreshold(Number(e.target.value))} style={{ flex: 1 }} />
                <span style={{ fontFamily: 'var(--mono)', fontSize: 13, fontWeight: 600, color: 'var(--t-text-bright)', minWidth: 40, textAlign: 'right' }}>{autoDismissThreshold}%</span>
              </div>
              <div style={{ fontSize: 11, color: 'var(--t-text-dim)', marginTop: 4 }}>
                Items with resolution quality below this threshold are auto-flagged for dismissal. Set to 0 to disable.
              </div>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <button className="btn btn-sm btn-primary" onClick={handleSaveSettings}>Save Settings</button>
            {settingsSaved && <span style={{ fontSize: 12, color: 'var(--t-success)' }}>Saved!</span>}
          </div>
        </div>
      )}
    </div>
  );
}
