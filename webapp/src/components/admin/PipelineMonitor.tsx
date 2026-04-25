import { useEffect, useRef, useState } from 'react';
import { api } from '../../api/client';
import type { QueueStats, QueueTask, PipelineExecution, PipelineSchedule } from '../../types';

const STEP_LABELS: Record<string, string> = {
  auto_tag: 'Auto-Tag', enrich: 'Enrich', engage: 'Engage', route: 'Route',
  audit: 'Audit', effort: 'Effort', notify: 'Notify',
  sla_breach_check: 'SLA Breach', sla_risk_check: 'SLA Risk',
  escalation_check: 'Escalation', audit_auto_close: 'Audit Close',
  tenant_health: 'Health Check', knowledge_gaps: 'Knowledge Gaps',
  kb_freshness: 'KB Freshness', kb_pipeline: 'KB Pipeline',
  trial_expiry: 'Trial Expiry',
};

const STEP_DESCRIPTIONS: Record<string, string> = {
  sla_breach_check: 'Scans all open tickets and marks any that have exceeded their SLA response or resolution deadline. Sends Teams alerts for P1/P2 breaches.',
  sla_risk_check: 'Identifies tickets approaching their SLA deadline within the next hour and flags them as "at risk" so agents can prioritise.',
  escalation_check: 'Finds tickets that have been open too long without agent activity and escalates them — raises priority, notifies the assigned agent, and logs the escalation.',
  audit_auto_close: 'Closes audit queue items that have been sitting in "pending review" for more than 30 days with no action taken.',
  tenant_health: 'Checks each tenant for warning signs: no ticket activity in 7+ days, plans expiring within 14 days, or unusually high SLA breach rates.',
  knowledge_gaps: 'Analyses recent tickets that Atlas could not answer confidently and surfaces them as knowledge gap entries so you know what KB articles to write next.',
  kb_freshness: 'Flags KB documents that have not been updated in 90+ days. Surfaces them as knowledge gap entries so stale content gets reviewed.',
  kb_pipeline: 'Runs the KB scraper pipeline — re-ingests content from enabled knowledge modules (Toast, Solink, etc.), re-chunks documents, and refreshes embeddings for RAG search.',
  trial_expiry: 'Checks all trial tenants daily and moves any whose 14-day trial has expired from Trial → Free tier, blocking AI features until they upgrade.',
};

const STATUS_COLORS: Record<string, { bg: string; text: string }> = {
  completed: { bg: 'rgba(52,211,153,0.15)', text: '#34d399' },
  success: { bg: 'rgba(52,211,153,0.15)', text: '#34d399' },
  running: { bg: 'rgba(96,165,250,0.15)', text: '#60a5fa' },
  pending: { bg: 'rgba(251,191,36,0.15)', text: '#fbbf24' },
  failed: { bg: 'rgba(239,68,68,0.15)', text: '#ef4444' },
  error: { bg: 'rgba(239,68,68,0.15)', text: '#ef4444' },
  cancelled: { bg: 'rgba(156,163,175,0.15)', text: '#9ca3af' },
};

function StatusBadge({ status }: { status: string }) {
  const c = STATUS_COLORS[status] || STATUS_COLORS.pending;
  return (
    <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4, fontWeight: 600,
                   background: c.bg, color: c.text, textTransform: 'capitalize' }}>
      {status}
    </span>
  );
}

function StepBadge({ step }: { step: string }) {
  return (
    <span style={{ fontSize: 11, padding: '2px 6px', borderRadius: 4,
                   background: 'rgba(139,92,246,0.15)', color: '#a78bfa' }}>
      {STEP_LABELS[step] || step}
    </span>
  );
}

function Toggle({ checked, onChange }: { checked: boolean; onChange: () => void }) {
  return (
    <button onClick={onChange} style={{
      width: 40, height: 22, borderRadius: 11, border: 'none', cursor: 'pointer', position: 'relative',
      background: checked ? '#34d399' : 'rgba(255,255,255,0.12)', transition: 'background 0.2s',
    }}>
      <span style={{
        position: 'absolute', top: 2, left: checked ? 20 : 2,
        width: 18, height: 18, borderRadius: '50%', background: '#fff',
        transition: 'left 0.2s', boxShadow: '0 1px 3px rgba(0,0,0,0.3)',
      }} />
    </button>
  );
}

function FilterSelect({ value, onChange, options, placeholder, accent = '#a78bfa', accentBg = 'rgba(139,92,246,0.15)' }: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  placeholder: string;
  accent?: string;
  accentBg?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);
  const selected = options.find(o => o.value === value);
  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button onClick={() => setOpen(o => !o)} style={{
        fontSize: 11, padding: '3px 24px 3px 8px', borderRadius: 4, border: '1px solid rgba(255,255,255,0.12)',
        background: value ? accentBg : 'rgba(255,255,255,0.05)',
        color: value ? accent : '#9ca3af', cursor: 'pointer', outline: 'none',
        display: 'flex', alignItems: 'center', gap: 6, position: 'relative', whiteSpace: 'nowrap',
      }}>
        {selected?.label || placeholder}
        <span style={{ position: 'absolute', right: 7, fontSize: 8, opacity: 0.7 }}>▼</span>
      </button>
      {open && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 3px)', left: 0, zIndex: 200,
          background: '#1e2130', border: '1px solid rgba(255,255,255,0.12)', borderRadius: 6,
          minWidth: '100%', boxShadow: '0 8px 24px rgba(0,0,0,0.6)', overflow: 'hidden',
        }}>
          {options.map(o => (
            <div key={o.value} onClick={() => { onChange(o.value); setOpen(false); }}
                 style={{
                   padding: '7px 14px', fontSize: 12, cursor: 'pointer', whiteSpace: 'nowrap',
                   color: o.value === value ? accent : '#d1d5db',
                   background: o.value === value ? accentBg : 'transparent',
                   transition: 'background 0.1s',
                 }}
                 onMouseEnter={e => { if (o.value !== value) (e.currentTarget as HTMLElement).style.background = 'rgba(255,255,255,0.06)'; }}
                 onMouseLeave={e => { if (o.value !== value) (e.currentTarget as HTMLElement).style.background = 'transparent'; }}
            >
              {o.label}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function formatDuration(ms: number | null) {
  if (ms == null) return '—';
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function toLocalTime(ts: string) {
  return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: true });
}

function toLocalDateTime(ts: string) {
  const d = new Date(ts);
  const today = new Date();
  const isToday = d.toDateString() === today.toDateString();
  if (isToday) return `Today ${toLocalTime(ts)}`;
  const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1);
  if (d.toDateString() === yesterday.toDateString()) return `Yesterday ${toLocalTime(ts)}`;
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' + toLocalTime(ts);
}

function minutesAgo(ts: string) {
  const diff = Date.now() - new Date(ts).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins === 1) return '1 min ago';
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs === 1) return '1 hour ago';
  if (hrs < 24) return `${hrs} hours ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

/** Convert UTC hour to local hour string like "2:00 AM" */
function utcHourToLocal(utcH: number, utcM: number): string {
  const d = new Date();
  d.setUTCHours(utcH, utcM, 0, 0);
  return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', hour12: true });
}

function cronToHuman(expr: string): string {
  const [minute, hour, dom, month, dow] = expr.split(' ');
  // Every N minutes
  if (minute.startsWith('*/') && hour === '*') {
    return `Every ${minute.slice(2)} minutes`;
  }
  // Specific minute at every hour
  if (!minute.includes('*') && !minute.includes('/') && hour === '*' && dom === '*') {
    return `Hourly at :${minute.padStart(2, '0')}`;
  }
  const h = parseInt(hour || '0');
  const m = parseInt(minute || '0');
  const localTime = utcHourToLocal(h, m);
  // Daily
  if (dom === '*' && month === '*' && dow === '*') {
    return `Daily at ${localTime}`;
  }
  // Weekly (specific dow) — cron 0=Sun but Python weekday() 0=Mon; n/a for display
  if (dom === '*' && month === '*' && dow !== '*') {
    const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    const dayName = days[parseInt(dow)] || dow;
    return `Weekly ${dayName} at ${localTime}`;
  }
  // Weekdays
  if (dow === '1-5') {
    return `Weekdays at ${localTime}`;
  }
  return expr;
}

function getNextRunMinutes(expr: string, lastRun: string | null): string | null {
  // Simple estimation based on cron pattern
  const [minute, hour] = expr.split(' ');
  const now = new Date();
  if (minute.startsWith('*/')) {
    const interval = parseInt(minute.slice(2));
    if (lastRun) {
      const last = new Date(lastRun);
      const next = new Date(last.getTime() + interval * 60000);
      const diff = Math.floor((next.getTime() - now.getTime()) / 60000);
      if (diff <= 0) return 'due now';
      if (diff === 1) return 'in 1 min';
      return `in ${diff} min`;
    }
    return `every ${interval} min`;
  }
  if (hour !== '*' && minute !== '*') {
    // Daily/weekly — find next occurrence
    const h = parseInt(hour); const m = parseInt(minute);
    const next = new Date(now);
    next.setHours(h, m, 0, 0);
    if (next <= now) next.setDate(next.getDate() + 1);
    const diff = Math.floor((next.getTime() - now.getTime()) / 60000);
    if (diff < 60) return `in ${diff} min`;
    if (diff < 1440) return `in ${Math.floor(diff / 60)}h`;
    return `in ${Math.floor(diff / 1440)}d`;
  }
  return null;
}

type HistoryFilter = 'all' | 'success' | 'error';
const PAGE_SIZE = 25;
const STEP_OPTIONS = Object.keys(STEP_LABELS);

export function PipelineMonitor() {
  const [stats, setStats] = useState<QueueStats | null>(null);
  const [active, setActive] = useState<QueueTask[]>([]);
  const [executions, setExecutions] = useState<PipelineExecution[]>([]);
  const [totalExecutions, setTotalExecutions] = useState(0);
  const [failures, setFailures] = useState<QueueTask[]>([]);
  const [schedules, setSchedules] = useState<PipelineSchedule[]>([]);
  const [historyFilter, setHistoryFilter] = useState<HistoryFilter>('all');
  const [stepFilter, setStepFilter] = useState<string>('');
  const [tenantFilter, setTenantFilter] = useState<string>('');
  const [tenants, setTenants] = useState<{id: number; name: string}[]>([]);
  const [page, setPage] = useState(0);
  const [expandedError, setExpandedError] = useState<number | null>(null);

  useEffect(() => {
    api.listTenants().then((d: any) => setTenants(Array.isArray(d) ? d : (d.tenants || []))).catch(() => {});
  }, []);

  const loadAll = async () => {
    try {
      const recentParams: Record<string, string> = { limit: String(PAGE_SIZE), offset: String(page * PAGE_SIZE) };
      if (historyFilter !== 'all') recentParams.status = historyFilter;
      if (stepFilter) recentParams.step = stepFilter;
      if (tenantFilter) recentParams.tenant_id = tenantFilter;

      const [s, a, r, f, sc] = await Promise.all([
        api.getQueueStats(),
        api.getQueueActive(),
        api.getQueueRecent(recentParams),
        api.getQueueFailures(),
        api.getQueueSchedules(),
      ]);
      setStats(s);
      setActive(a.tasks);
      setExecutions(r.executions);
      setTotalExecutions(r.total);
      setFailures(f.tasks);
      setSchedules(sc.schedules);
    } catch {}
  };

  useEffect(() => { loadAll(); const t = setInterval(loadAll, 5000); return () => clearInterval(t); }, [historyFilter, stepFilter, tenantFilter, page]);

  const handleRetry = async (id: number) => {
    await api.retryQueueTask(id);
    loadAll();
  };

  const handleCancel = async (id: number) => {
    await api.cancelQueueTask(id);
    loadAll();
  };

  const handleToggleSchedule = async (id: number, enabled: boolean) => {
    await api.toggleQueueSchedule(id, enabled);
    loadAll();
  };

  return (
    <div style={{ padding: '0 4px' }}>
      {/* Stats Bar */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 24 }}>
        {[
          { label: 'Queue Depth', value: stats?.queue_depth ?? 0, color: '#fbbf24' },
          { label: 'Running', value: `${stats?.running ?? 0} (${stats?.running_llm ?? 0} LLM)`, color: '#60a5fa' },
          { label: 'Completed / hr', value: stats?.completed_last_hour ?? 0, color: '#34d399' },
          { label: 'Failed / hr', value: stats?.failed_last_hour ?? 0, color: (stats?.failed_last_hour ?? 0) > 0 ? '#ef4444' : '#9ca3af' },
        ].map((s, i) => (
          <div key={i} style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: '16px 20px',
                                border: '1px solid rgba(255,255,255,0.06)' }}>
            <div style={{ fontSize: 11, color: '#9ca3af', textTransform: 'uppercase', marginBottom: 4 }}>{s.label}</div>
            <div style={{ fontSize: 24, fontWeight: 700, color: s.color }}>{s.value}</div>
          </div>
        ))}
      </div>

      {/* Active Tasks */}
      {active.length > 0 && (
        <div style={{ marginBottom: 24 }}>
          <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>Active Tasks</h3>
          <div style={{ background: 'rgba(96,165,250,0.05)', borderRadius: 8, border: '1px solid rgba(96,165,250,0.15)', overflow: 'hidden' }}>
            {active.map(t => (
              <div key={t.id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '8px 16px',
                                      borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                <StatusBadge status="running" />
                <StepBadge step={t.step_name} />
                <span style={{ color: '#d1d5db', fontSize: 13 }}>{t.ticket_number || '(cron)'}</span>
                <span style={{ marginLeft: 'auto', fontSize: 12, color: '#9ca3af' }}>
                  {t.started_at ? minutesAgo(t.started_at) : '—'}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Failed Tasks */}
      {failures.length > 0 && (
        <div style={{ marginBottom: 24 }}>
          <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 8, color: '#ef4444' }}>
            Failed Tasks ({failures.length})
          </h3>
          <div style={{ background: 'rgba(239,68,68,0.05)', borderRadius: 8, border: '1px solid rgba(239,68,68,0.15)', overflow: 'hidden' }}>
            {failures.map(t => (
              <div key={t.id} style={{ padding: '8px 16px', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <StatusBadge status="failed" />
                  <StepBadge step={t.step_name} />
                  <span style={{ color: '#d1d5db', fontSize: 13 }}>{t.ticket_number || '(cron)'}</span>
                  <span style={{ fontSize: 11, color: '#9ca3af' }}>
                    {t.attempts}/{t.max_attempts} attempts
                  </span>
                  <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
                    <button className="btn btn-xs btn-primary" onClick={() => handleRetry(t.id)}>Retry</button>
                    <button className="btn btn-xs btn-ghost" onClick={() => setExpandedError(expandedError === t.id ? null : t.id)}>
                      {expandedError === t.id ? 'Hide' : 'Error'}
                    </button>
                  </div>
                </div>
                {expandedError === t.id && t.last_error && (
                  <pre style={{ fontSize: 11, color: '#ef4444', marginTop: 8, padding: 8,
                                background: 'rgba(0,0,0,0.3)', borderRadius: 4, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                    {t.last_error}
                  </pre>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Execution History */}
      <div style={{ marginBottom: 24 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8, flexWrap: 'wrap' }}>
          <h3 style={{ fontSize: 14, fontWeight: 600, margin: 0 }}>Execution History</h3>
          <span style={{ fontSize: 12, color: '#6b7280' }}>
            {totalExecutions.toLocaleString()} total
          </span>
          <div style={{ display: 'flex', gap: 4 }}>
            {(['all', 'success', 'error'] as HistoryFilter[]).map(f => (
              <button key={f} onClick={() => { setHistoryFilter(f); setPage(0); }}
                      style={{ fontSize: 11, padding: '2px 10px', borderRadius: 4, border: 'none', cursor: 'pointer',
                               background: historyFilter === f ? 'rgba(139,92,246,0.2)' : 'rgba(255,255,255,0.05)',
                               color: historyFilter === f ? '#a78bfa' : '#9ca3af' }}>
                {f === 'all' ? 'All' : f === 'success' ? 'Completed' : 'Failed'}
              </button>
            ))}
          </div>
          <FilterSelect
            value={stepFilter}
            onChange={v => { setStepFilter(v); setPage(0); }}
            placeholder="All Steps"
            options={[{ value: '', label: 'All Steps' }, ...STEP_OPTIONS.map(s => ({ value: s, label: STEP_LABELS[s] }))]}
          />
          <FilterSelect
            value={tenantFilter}
            onChange={v => { setTenantFilter(v); setPage(0); }}
            placeholder="All Tenants"
            options={[{ value: '', label: 'All Tenants' }, ...tenants.map(t => ({ value: String(t.id), label: t.name }))]}
            accent="#60a5fa"
            accentBg="rgba(96,165,250,0.15)"
          />
        </div>
        <div style={{ background: 'rgba(255,255,255,0.02)', borderRadius: 8, border: '1px solid rgba(255,255,255,0.06)', overflow: 'hidden' }}>
          <div style={{ maxHeight: 520, overflowY: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead style={{ position: 'sticky', top: 0, background: 'var(--bg-primary, #111)' }}>
                <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
                  <th style={{ padding: '8px 12px', textAlign: 'left', color: '#9ca3af', fontWeight: 500 }}>Time</th>
                  <th style={{ padding: '8px 12px', textAlign: 'left', color: '#9ca3af', fontWeight: 500 }}>Tenant</th>
                  <th style={{ padding: '8px 12px', textAlign: 'left', color: '#9ca3af', fontWeight: 500 }}>Ticket</th>
                  <th style={{ padding: '8px 12px', textAlign: 'left', color: '#9ca3af', fontWeight: 500 }}>Step</th>
                  <th style={{ padding: '8px 12px', textAlign: 'left', color: '#9ca3af', fontWeight: 500 }}>Status</th>
                  <th style={{ padding: '8px 12px', textAlign: 'right', color: '#9ca3af', fontWeight: 500 }}>Duration</th>
                  <th style={{ padding: '8px 12px', textAlign: 'center', color: '#9ca3af', fontWeight: 500 }}>Attempt</th>
                </tr>
              </thead>
              <tbody>
                {executions.map(e => (
                  <tr key={e.id} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)',
                                          cursor: (e.error_message || e.output_summary) ? 'pointer' : 'default' }}
                      onClick={() => (e.error_message || e.output_summary) && setExpandedError(expandedError === -e.id ? null : -e.id)}>
                    <td style={{ padding: '6px 12px', color: '#d1d5db' }} title={new Date(e.created_at).toLocaleString()}>{toLocalDateTime(e.created_at)}</td>
                    <td style={{ padding: '6px 12px', color: '#9ca3af', fontSize: 12 }}>
                      {e.tenant_id ? (tenants.find(t => t.id === e.tenant_id)?.name ?? `#${e.tenant_id}`) : '—'}
                    </td>
                    <td style={{ padding: '6px 12px', color: '#d1d5db' }}>{e.ticket_number || '—'}</td>
                    <td style={{ padding: '6px 12px' }}><StepBadge step={e.step_name} /></td>
                    <td style={{ padding: '6px 12px' }}>
                      <StatusBadge status={e.status} />
                      {e.error_message && (
                        <span style={{ fontSize: 10, marginLeft: 6, color: '#ef4444', cursor: 'pointer' }}>
                          {expandedError === -e.id ? '▼' : '▶'} error
                        </span>
                      )}
                      {!e.error_message && e.output_summary && (
                        <span style={{ fontSize: 10, marginLeft: 6, color: '#6b7280', cursor: 'pointer' }}>
                          {expandedError === -e.id ? '▼' : '▶'} detail
                        </span>
                      )}
                    </td>
                    <td style={{ padding: '6px 12px', textAlign: 'right', color: '#9ca3af' }}>{formatDuration(e.duration_ms)}</td>
                    <td style={{ padding: '6px 12px', textAlign: 'center', color: '#9ca3af' }}>{e.attempts}</td>
                  </tr>
                ))}
                {executions.map(e => expandedError === -e.id && (e.error_message || e.output_summary) ? (
                  <tr key={`err-${e.id}`}>
                    <td colSpan={7} style={{ padding: '0 12px 8px' }}>
                      {e.output_summary && (
                        <div style={{ fontSize: 11, color: '#9ca3af', padding: '6px 10px',
                                      background: 'rgba(255,255,255,0.03)', borderRadius: 4,
                                      marginBottom: e.error_message ? 6 : 0 }}>
                          {e.output_summary}
                        </div>
                      )}
                      {e.error_message && (
                        <div style={{ position: 'relative' }}>
                          <pre style={{ fontSize: 11, color: '#ef4444', padding: '10px 36px 10px 10px',
                                        background: 'rgba(239,68,68,0.08)', borderRadius: 4,
                                        whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0 }}>
                            {e.error_message}
                          </pre>
                          <button
                            title="Copy error"
                            onClick={(ev) => { ev.stopPropagation(); navigator.clipboard.writeText(e.error_message!); }}
                            style={{ position: 'absolute', top: 6, right: 6, background: 'rgba(255,255,255,0.08)',
                                     border: 'none', borderRadius: 4, color: '#9ca3af', cursor: 'pointer',
                                     padding: '2px 6px', fontSize: 11, lineHeight: 1 }}>
                            ⎘
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                ) : null)}
                {executions.length === 0 && (
                  <tr><td colSpan={7} style={{ padding: 24, textAlign: 'center', color: '#6b7280' }}>No executions found</td></tr>
                )}
              </tbody>
            </table>
          </div>
          {/* Pagination */}
          {totalExecutions > PAGE_SIZE && (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                          padding: '8px 12px', borderTop: '1px solid rgba(255,255,255,0.08)' }}>
              <span style={{ fontSize: 12, color: '#6b7280' }}>
                {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, totalExecutions)} of {totalExecutions.toLocaleString()}
              </span>
              <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                <button onClick={() => setPage(0)} disabled={page === 0}
                        style={{ fontSize: 11, padding: '3px 8px', borderRadius: 4, border: 'none', cursor: page === 0 ? 'default' : 'pointer',
                                 background: 'rgba(255,255,255,0.05)', color: page === 0 ? '#4b5563' : '#9ca3af' }}>
                  First
                </button>
                <button onClick={() => setPage(p => p - 1)} disabled={page === 0}
                        style={{ fontSize: 11, padding: '3px 8px', borderRadius: 4, border: 'none', cursor: page === 0 ? 'default' : 'pointer',
                                 background: 'rgba(255,255,255,0.05)', color: page === 0 ? '#4b5563' : '#9ca3af' }}>
                  Prev
                </button>
                <span style={{ fontSize: 12, color: '#9ca3af', padding: '0 8px' }}>
                  Page {page + 1} of {Math.ceil(totalExecutions / PAGE_SIZE)}
                </span>
                <button onClick={() => setPage(p => p + 1)} disabled={(page + 1) * PAGE_SIZE >= totalExecutions}
                        style={{ fontSize: 11, padding: '3px 8px', borderRadius: 4, border: 'none',
                                 cursor: (page + 1) * PAGE_SIZE >= totalExecutions ? 'default' : 'pointer',
                                 background: 'rgba(255,255,255,0.05)',
                                 color: (page + 1) * PAGE_SIZE >= totalExecutions ? '#4b5563' : '#9ca3af' }}>
                  Next
                </button>
                <button onClick={() => setPage(Math.ceil(totalExecutions / PAGE_SIZE) - 1)}
                        disabled={(page + 1) * PAGE_SIZE >= totalExecutions}
                        style={{ fontSize: 11, padding: '3px 8px', borderRadius: 4, border: 'none',
                                 cursor: (page + 1) * PAGE_SIZE >= totalExecutions ? 'default' : 'pointer',
                                 background: 'rgba(255,255,255,0.05)',
                                 color: (page + 1) * PAGE_SIZE >= totalExecutions ? '#4b5563' : '#9ca3af' }}>
                  Last
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Cron Schedules */}
      <div>
        <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>Scheduled Tasks</h3>
        <div style={{ background: 'rgba(255,255,255,0.02)', borderRadius: 8, border: '1px solid rgba(255,255,255,0.06)', overflow: 'hidden' }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(140px, 200px) minmax(150px, 1fr) minmax(110px, 160px) minmax(110px, 160px) 44px', alignItems: 'center',
                        gap: 12, padding: '10px 20px', borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
            <span style={{ fontSize: 11, color: '#6b7280', textTransform: 'uppercase', fontWeight: 600 }}>Task</span>
            <span style={{ fontSize: 11, color: '#6b7280', textTransform: 'uppercase', fontWeight: 600 }}>Schedule</span>
            <span style={{ fontSize: 11, color: '#6b7280', textTransform: 'uppercase', fontWeight: 600 }}>Last Run</span>
            <span style={{ fontSize: 11, color: '#6b7280', textTransform: 'uppercase', fontWeight: 600 }}>Next Run</span>
            <span style={{ fontSize: 11, color: '#6b7280', textTransform: 'uppercase', fontWeight: 600 }}>On</span>
          </div>
          {schedules.map(s => {
            const nextRun = getNextRunMinutes(s.cron_expression, s.last_enqueued_at);
            const desc = STEP_DESCRIPTIONS[s.step_name];
            return (
              <div key={s.id} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                <div style={{ display: 'grid', gridTemplateColumns: 'minmax(140px, 200px) minmax(150px, 1fr) minmax(110px, 160px) minmax(110px, 160px) 44px', alignItems: 'start',
                              gap: 12, padding: '14px 20px' }}>
                  <div>
                    <StepBadge step={s.step_name} />
                  </div>
                  <div style={{ fontSize: 12, color: '#d1d5db', fontWeight: 500, paddingTop: 2 }}>
                    {cronToHuman(s.cron_expression)}
                  </div>
                  <div style={{ fontSize: 12, color: '#9ca3af', paddingTop: 2 }}>
                    {s.last_enqueued_at ? minutesAgo(s.last_enqueued_at) : 'Never run'}
                  </div>
                  <div style={{ fontSize: 12, color: nextRun?.startsWith('due') ? '#fbbf24' : '#60a5fa', fontWeight: 500, paddingTop: 2 }}>
                    {nextRun && s.enabled ? nextRun : s.enabled ? '—' : 'Paused'}
                  </div>
                  <Toggle checked={s.enabled} onChange={() => handleToggleSchedule(s.id, !s.enabled)} />
                </div>
                {desc && (
                  <div style={{ padding: '0 20px 12px 20px', fontSize: 11, color: '#6b7280', lineHeight: 1.6 }}>
                    {desc}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
