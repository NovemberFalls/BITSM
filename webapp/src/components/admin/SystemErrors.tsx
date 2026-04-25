import { useEffect, useRef, useState } from 'react';
import { api } from '../../api/client';

// ─── Types ────────────────────────────────────────────────────────────────────

interface SystemError {
  id: number;
  occurred_at: string;
  severity: 'error' | 'warning' | 'critical';
  route: string | null;
  method: string | null;
  error_type: string | null;
  message: string | null;
  stack_trace: string | null;
  tenant_id: number | null;
  tenant_name: string | null;
  user_id: number | null;
  user_name: string | null;
  resolved: boolean;
  resolved_at: string | null;
  notes: string | null;
}

type FilterMode = 'all' | 'unresolved' | 'resolved';

// ─── Helpers ──────────────────────────────────────────────────────────────────

function toLocalDateTime(ts: string): string {
  const d = new Date(ts);
  const today = new Date();
  const isToday = d.toDateString() === today.toDateString();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  const isYesterday = d.toDateString() === yesterday.toDateString();
  const timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: true });
  if (isToday) return `Today ${timeStr}`;
  if (isYesterday) return `Yesterday ${timeStr}`;
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' + timeStr;
}

function truncate(s: string | null, max = 80): string {
  if (!s) return '—';
  return s.length > max ? s.slice(0, max) + '…' : s;
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function SeverityBadge({ severity }: { severity: string }) {
  const isCritical = severity === 'critical';
  const isWarning  = severity === 'warning';
  const color  = isWarning
    ? 'var(--t-warning, #dddd44)'
    : 'var(--t-error, #ff4444)';
  return (
    <span style={{
      fontSize: 11,
      padding: '2px 8px',
      borderRadius: 4,
      fontWeight: isCritical ? 800 : 600,
      background: `color-mix(in srgb, ${color} 15%, transparent)`,
      color,
      textTransform: 'uppercase',
      letterSpacing: '0.04em',
    }}>
      {severity}
    </span>
  );
}

function ResolvedBadge() {
  return (
    <span style={{
      fontSize: 11,
      padding: '2px 8px',
      borderRadius: 4,
      fontWeight: 600,
      background: 'var(--t-accent-bg, rgba(68,221,68,0.10))',
      color: 'var(--t-accent-text, var(--t-accent, #44dd44))',
    }}>
      Resolved
    </span>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

export function SystemErrors() {
  const [errors, setErrors]         = useState<SystemError[]>([]);
  const [total, setTotal]           = useState(0);
  const [unresolved, setUnresolved] = useState(0);
  const [loading, setLoading]       = useState(true);
  const [filter, setFilter]         = useState<FilterMode>('unresolved');
  const [expanded, setExpanded]     = useState<number | null>(null);
  const [resolving, setResolving]   = useState<number | null>(null);
  const [notesMap, setNotesMap]     = useState<Record<number, string>>({});
  const [page, setPage]             = useState(0);
  const PAGE_SIZE = 50;

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const params: Record<string, string> = {
        limit:  String(PAGE_SIZE),
        offset: String(page * PAGE_SIZE),
      };
      if (filter === 'resolved')   params.resolved = 'true';
      if (filter === 'unresolved') params.resolved = 'false';

      const data = await api.listSystemErrors(params);
      setErrors(data.errors ?? []);
      setTotal(data.total ?? 0);

      // Maintain unresolved badge count regardless of active filter
      if (filter === 'unresolved') {
        setUnresolved(data.total ?? 0);
      } else {
        try {
          const u = await api.listSystemErrors({ resolved: 'false', limit: '1', offset: '0' });
          setUnresolved(u.total ?? 0);
        } catch {
          // not critical
        }
      }
    } catch {
      // fail silently on background refresh
    } finally {
      setLoading(false);
    }
  };

  // Reset to page 0 when filter changes
  useEffect(() => {
    setPage(0);
  }, [filter]);

  useEffect(() => {
    load();
    intervalRef.current = setInterval(() => load(true), 30_000);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [filter, page]);

  const handleResolve = async (id: number) => {
    setResolving(id);
    try {
      const notes = notesMap[id] || null;
      await api.resolveSystemError(id, notes);
      setExpanded(null);
      await load(true);
    } catch {
      // ignore
    } finally {
      setResolving(null);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm('Permanently delete this error record?')) return;
    try {
      await api.deleteSystemError(id);
      await load(true);
    } catch {
      // ignore
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div style={{ padding: '0 4px' }}>

      {/* Header row */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20, flexWrap: 'wrap',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <h2 style={{ fontSize: 18, fontWeight: 700, color: 'var(--t-text-bright)', margin: 0 }}>
            System Log
          </h2>
          {unresolved > 0 && (
            <span style={{
              fontSize: 11, fontWeight: 700,
              padding: '2px 8px', borderRadius: 10,
              background: `color-mix(in srgb, var(--t-error, #ff4444) 18%, transparent)`,
              color: 'var(--t-error, #ff4444)',
              border: `1px solid color-mix(in srgb, var(--t-error, #ff4444) 35%, transparent)`,
            }}>
              {unresolved} unresolved
            </span>
          )}
        </div>

        {/* Filter toggle */}
        <div style={{ display: 'flex', gap: 4, marginLeft: 'auto' }}>
          {(['all', 'unresolved', 'resolved'] as FilterMode[]).map(f => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              style={{
                fontSize: 12, padding: '4px 12px', borderRadius: 6, cursor: 'pointer',
                border: filter === f
                  ? '1px solid var(--t-accent-border, rgba(68,221,68,0.25))'
                  : '1px solid var(--t-border)',
                background: filter === f
                  ? 'var(--t-accent-bg, rgba(68,221,68,0.10))'
                  : 'transparent',
                color: filter === f
                  ? 'var(--t-accent-text, var(--t-accent, #44dd44))'
                  : 'var(--t-text-muted)',
                fontWeight: filter === f ? 600 : 400,
                textTransform: 'capitalize',
                transition: 'all 0.15s',
              }}
            >
              {f}
            </button>
          ))}
        </div>
      </div>

      {/* Table */}
      {loading ? (
        <div style={{ color: 'var(--t-text-muted)', fontSize: 13, padding: '24px 0' }}>
          Loading…
        </div>
      ) : errors.length === 0 ? (
        <div style={{
          background: 'rgba(255,255,255,0.02)', border: '1px solid var(--t-border)',
          borderRadius: 8, padding: '32px 24px', textAlign: 'center',
          color: 'var(--t-text-muted)', fontSize: 13,
        }}>
          {filter === 'unresolved'
            ? 'No unresolved errors — system is clean.'
            : 'No errors match the current filter.'}
        </div>
      ) : (
        <div style={{ border: '1px solid var(--t-border)', borderRadius: 8, overflow: 'hidden' }}>

          {/* Column headers */}
          <div style={{
            display: 'grid',
            gridTemplateColumns: '160px 80px 180px 160px 1fr 120px 90px',
            padding: '8px 16px',
            background: 'var(--t-panel-alt)',
            borderBottom: '1px solid var(--t-border)',
            fontSize: 11, fontWeight: 700,
            color: 'var(--t-text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em',
          }}>
            <span>Timestamp</span>
            <span>Severity</span>
            <span>Route</span>
            <span>Error Type</span>
            <span>Message</span>
            <span>Tenant</span>
            <span>Status</span>
          </div>

          {errors.map((err) => (
            <div key={err.id}>
              {/* Summary row */}
              <div
                onClick={() => setExpanded(expanded === err.id ? null : err.id)}
                style={{
                  display: 'grid',
                  gridTemplateColumns: '160px 80px 180px 160px 1fr 120px 90px',
                  padding: '10px 16px',
                  borderBottom: '1px solid var(--t-border)',
                  cursor: 'pointer',
                  background: expanded === err.id ? 'var(--t-panel-alt)' : 'transparent',
                  transition: 'background 0.1s',
                  alignItems: 'center',
                }}
                onMouseEnter={e => {
                  if (expanded !== err.id)
                    (e.currentTarget as HTMLElement).style.background = 'var(--t-hover)';
                }}
                onMouseLeave={e => {
                  if (expanded !== err.id)
                    (e.currentTarget as HTMLElement).style.background = 'transparent';
                }}
              >
                <span style={{ fontSize: 12, color: 'var(--t-text-muted)' }}>
                  {err.occurred_at ? toLocalDateTime(err.occurred_at) : '—'}
                </span>
                <span><SeverityBadge severity={err.severity} /></span>
                <span style={{
                  fontSize: 12, color: 'var(--t-text)',
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  fontFamily: 'var(--mono)',
                }}>
                  {err.method && (
                    <span style={{ color: 'var(--t-text-muted)', marginRight: 4 }}>{err.method}</span>
                  )}
                  {truncate(err.route, 30) || '—'}
                </span>
                <span style={{
                  fontSize: 12, color: 'var(--t-warning, #dddd44)',
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  fontFamily: 'var(--mono)',
                }}>
                  {err.error_type || '—'}
                </span>
                <span style={{
                  fontSize: 12, color: 'var(--t-text)',
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                  {truncate(err.message, 80)}
                </span>
                <span style={{
                  fontSize: 12, color: 'var(--t-text-muted)',
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                  {err.tenant_name || (err.tenant_id ? `#${err.tenant_id}` : '—')}
                </span>
                <span>
                  {err.resolved
                    ? <ResolvedBadge />
                    : <span style={{ fontSize: 12, color: 'var(--t-text-muted)' }}>Open</span>
                  }
                </span>
              </div>

              {/* Expanded detail panel */}
              {expanded === err.id && (
                <div style={{
                  padding: '16px 20px',
                  background: 'var(--t-panel)',
                  borderBottom: '1px solid var(--t-border)',
                }}>
                  {/* Meta */}
                  <div style={{
                    display: 'flex', flexWrap: 'wrap', gap: '8px 24px',
                    marginBottom: 14, fontSize: 12, color: 'var(--t-text-muted)',
                  }}>
                    <span>
                      <strong style={{ color: 'var(--t-text)' }}>ID:</strong> {err.id}
                    </span>
                    {err.user_name && (
                      <span>
                        <strong style={{ color: 'var(--t-text)' }}>User:</strong> {err.user_name}
                      </span>
                    )}
                    {err.route && (
                      <span style={{ fontFamily: 'var(--mono)' }}>
                        <strong style={{ color: 'var(--t-text)' }}>Route:</strong>{' '}
                        {err.method} {err.route}
                      </span>
                    )}
                    {err.resolved_at && (
                      <span>
                        <strong style={{ color: 'var(--t-text)' }}>Resolved:</strong>{' '}
                        {toLocalDateTime(err.resolved_at)}
                      </span>
                    )}
                    {err.notes && (
                      <span>
                        <strong style={{ color: 'var(--t-text)' }}>Notes:</strong> {err.notes}
                      </span>
                    )}
                  </div>

                  {/* Stack trace */}
                  {err.stack_trace && (
                    <pre style={{
                      fontSize: 11, lineHeight: 1.6,
                      color: 'var(--t-error, #ff4444)',
                      background: 'rgba(0,0,0,0.35)',
                      border: `1px solid color-mix(in srgb, var(--t-error, #ff4444) 20%, transparent)`,
                      borderRadius: 6,
                      padding: '12px 14px',
                      whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                      maxHeight: 360, overflowY: 'auto',
                      fontFamily: 'var(--mono)',
                      marginBottom: 14,
                    }}>
                      {err.stack_trace}
                    </pre>
                  )}

                  {/* Actions */}
                  {!err.resolved && (
                    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, flexWrap: 'wrap' }}>
                      <textarea
                        value={notesMap[err.id] ?? ''}
                        onChange={e => setNotesMap(prev => ({ ...prev, [err.id]: e.target.value }))}
                        placeholder="Optional resolution note…"
                        rows={2}
                        style={{
                          flex: 1, minWidth: 200, maxWidth: 440,
                          fontSize: 12, padding: '6px 10px',
                          background: 'var(--t-input-bg)',
                          border: '1px solid var(--t-border)',
                          borderRadius: 6,
                          color: 'var(--t-text)',
                          resize: 'vertical',
                          fontFamily: 'var(--font)',
                        }}
                      />
                      <button
                        className="btn btn-primary btn-sm"
                        onClick={() => handleResolve(err.id)}
                        disabled={resolving === err.id}
                        style={{ whiteSpace: 'nowrap' }}
                      >
                        {resolving === err.id ? 'Resolving…' : 'Mark Resolved'}
                      </button>
                      <button
                        className="btn btn-ghost btn-sm"
                        onClick={() => handleDelete(err.id)}
                        style={{ color: 'var(--t-error, #ff4444)', whiteSpace: 'nowrap' }}
                      >
                        Delete
                      </button>
                    </div>
                  )}

                  {err.resolved && (
                    <button
                      className="btn btn-ghost btn-sm"
                      onClick={() => handleDelete(err.id)}
                      style={{ color: 'var(--t-error, #ff4444)' }}
                    >
                      Delete
                    </button>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
          marginTop: 16, fontSize: 12, color: 'var(--t-text-muted)',
        }}>
          <button
            className="btn btn-ghost btn-sm"
            disabled={page === 0}
            onClick={() => setPage(p => p - 1)}
          >
            Prev
          </button>
          <span>Page {page + 1} of {totalPages} ({total.toLocaleString()} total)</span>
          <button
            className="btn btn-ghost btn-sm"
            disabled={page >= totalPages - 1}
            onClick={() => setPage(p => p + 1)}
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
