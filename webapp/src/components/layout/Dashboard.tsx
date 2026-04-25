import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import { useUIStore } from '../../store/uiStore';
import { PRIORITY_OPTIONS, STATUS_OPTIONS } from '../../types';
import { pushUrl } from '../../utils/url';

interface Metrics {
  open: number;
  pending: number;
  resolved: number;
  closed: number;
}

function priorityLabel(p: string): string {
  return PRIORITY_OPTIONS.find((o) => o.value === p)?.label || p;
}

function statusLabel(s: string): string {
  return STATUS_OPTIONS.find((o) => o.value === s)?.label || s;
}

// Module-level cache so data survives component unmount
let _cachedMetrics: Metrics = { open: 0, pending: 0, resolved: 0, closed: 0 };
let _cachedRecent: any[] = [];
let _hasLoaded = false;

export function Dashboard() {
  const [metrics, setMetrics] = useState<Metrics>(_cachedMetrics);
  const [recentTickets, setRecentTickets] = useState<any[]>(_cachedRecent);
  const [loading, setLoading] = useState(!_hasLoaded);
  const { setView, openTicketDetail } = useUIStore();

  const fetchDashboard = () => {
    Promise.all([
      api.listTickets({ status: 'open', limit: '0' }),
      api.listTickets({ status: 'pending', limit: '0' }),
      api.listTickets({ status: 'resolved', limit: '0' }),
      api.listTickets({ status: 'closed_not_resolved', limit: '0' }),
      api.listTickets({ limit: '5', sort_by: 'created_at', sort_dir: 'desc' }),
    ]).then(([open, pending, resolved, closed, recent]) => {
      const m = { open: open.total, pending: pending.total, resolved: resolved.total, closed: closed.total };
      _cachedMetrics = m;
      _cachedRecent = recent.tickets;
      _hasLoaded = true;
      setMetrics(m);
      setRecentTickets(recent.tickets);
    }).catch(() => {}).finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchDashboard();
  }, []);

  // Poll every 15s for seamless updates
  useEffect(() => {
    const interval = setInterval(fetchDashboard, 15000);
    return () => clearInterval(interval);
  }, []);

  const handleTicketClick = (id: number) => {
    setView('tickets');
    pushUrl('/tickets');
    openTicketDetail(id);
  };

  const handleMetricClick = () => {
    setView('tickets');
    pushUrl('/tickets');
  };

  return (
    <div>
      <div className="metrics-grid">
        <MetricCard label="Open" value={metrics.open} color="var(--t-info)" loading={loading} onClick={handleMetricClick} />
        <MetricCard label="Pending" value={metrics.pending} color="var(--t-accent-text)" loading={loading} onClick={handleMetricClick} />
        <MetricCard label="Resolved" value={metrics.resolved} color="var(--t-success)" loading={loading} onClick={handleMetricClick} />
        <MetricCard label="Closed (NR)" value={metrics.closed} color="var(--t-error)" loading={loading} onClick={handleMetricClick} />
      </div>

      <div className="section-header">
        <h3 className="section-title">Recent Tickets</h3>
        <button className="btn btn-ghost btn-sm" onClick={() => { setView('tickets'); pushUrl('/tickets'); }}>
          View all
        </button>
      </div>

      <div className="recent-tickets">
        {recentTickets.map((t) => (
          <div key={t.id} className="card card-clickable" onClick={() => handleTicketClick(t.id)}>
            <div className="recent-ticket-left">
              <span className="mono-text">{t.ticket_number}</span>
              <span className="recent-ticket-subject">{t.subject}</span>
            </div>
            <div className="recent-ticket-right">
              <span className={`badge badge-${t.priority}`}>{priorityLabel(t.priority)}</span>
              <span className={`badge badge-${t.status}`}>{statusLabel(t.status)}</span>
            </div>
          </div>
        ))}
        {!loading && recentTickets.length === 0 && (
          <div className="empty-state">
            <div className="empty-state-icon">&#x25C8;</div>
            <div className="empty-state-title">Welcome to Helpdesk</div>
            <div className="empty-state-text">No tickets yet. Navigate to Tickets to create your first one.</div>
          </div>
        )}
      </div>
    </div>
  );
}

function MetricCard({ label, value, color, loading, onClick }: { label: string; value: number; color: string; loading: boolean; onClick: () => void }) {
  return (
    <div className="metric-card card-clickable" onClick={onClick}>
      <div className="metric-card-label">{label}</div>
      <div className="metric-card-value" style={{ color }}>
        {loading ? <span className="metric-loading">&mdash;</span> : value}
      </div>
    </div>
  );
}
