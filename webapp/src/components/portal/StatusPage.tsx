import { useEffect, useState } from 'react';
import { api } from '../../api/client';

interface StatusUpdate {
  id: number;
  body: string;
  status: string;
  author_name: string | null;
  created_at: string;
}

interface StatusIncident {
  id: number;
  title: string;
  body: string;
  status: string;
  severity: string;
  started_at: string;
  scheduled_end: string | null;
  resolved_at: string | null;
  author_name: string | null;
  created_at: string;
  updated_at: string;
  updates?: StatusUpdate[];
}

const SEVERITY_CONFIG: Record<string, { label: string; className: string }> = {
  critical:    { label: 'Critical',    className: 'status-severity-critical' },
  major:       { label: 'Major',       className: 'status-severity-major' },
  minor:       { label: 'Minor',       className: 'status-severity-minor' },
  maintenance: { label: 'Maintenance', className: 'status-severity-maintenance' },
};

const STATUS_LABELS: Record<string, string> = {
  scheduled:     'Scheduled',
  investigating: 'Investigating',
  identified:    'Identified',
  monitoring:    'Monitoring',
  resolved:      'Resolved',
};

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', {
    month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}

export function StatusPage({ onBack }: { onBack: () => void }) {
  const [incidents, setIncidents] = useState<StatusIncident[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  useEffect(() => {
    api.listStatusIncidents()
      .then(setIncidents)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const handleExpand = async (id: number) => {
    if (expandedId === id) {
      setExpandedId(null);
      return;
    }
    setExpandedId(id);
    try {
      const detail = await api.getStatusIncident(id);
      setIncidents((prev) => prev.map((i) => (i.id === id ? { ...i, updates: detail.updates } : i)));
    } catch { /* ignore */ }
  };

  const active = incidents.filter((i) => i.status !== 'resolved');
  const resolved = incidents.filter((i) => i.status === 'resolved');
  const allOperational = active.length === 0;

  return (
    <div className="status-page">
      <div className="status-page-header">
        <button className="btn btn-ghost btn-sm" onClick={onBack}>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M10 12L6 8l4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
          Back
        </button>
        <h2 className="status-page-title">System Status</h2>
      </div>

      {loading ? (
        <div className="status-page-loading">Loading status...</div>
      ) : (
        <>
          {/* Overall status banner */}
          <div className={`status-banner ${allOperational ? 'status-banner-ok' : 'status-banner-issue'}`}>
            <div className="status-banner-dot" />
            <span>{allOperational ? 'All Systems Operational' : `${active.length} Active Incident${active.length > 1 ? 's' : ''}`}</span>
          </div>

          {/* Active incidents */}
          {active.length > 0 && (
            <div className="status-section">
              <h3 className="status-section-title">Active Incidents</h3>
              {active.map((incident) => (
                <IncidentCard
                  key={incident.id}
                  incident={incident}
                  expanded={expandedId === incident.id}
                  onToggle={() => handleExpand(incident.id)}
                />
              ))}
            </div>
          )}

          {/* Resolved (last 7 days) */}
          {resolved.length > 0 && (
            <div className="status-section">
              <h3 className="status-section-title">Recently Resolved</h3>
              {resolved.map((incident) => (
                <IncidentCard
                  key={incident.id}
                  incident={incident}
                  expanded={expandedId === incident.id}
                  onToggle={() => handleExpand(incident.id)}
                />
              ))}
            </div>
          )}

          {incidents.length === 0 && (
            <div className="status-page-empty">No incidents to report. All systems are running normally.</div>
          )}
        </>
      )}
    </div>
  );
}

function IncidentCard({ incident, expanded, onToggle }: {
  incident: StatusIncident;
  expanded: boolean;
  onToggle: () => void;
}) {
  const sev = SEVERITY_CONFIG[incident.severity] || SEVERITY_CONFIG.minor;
  const isResolved = incident.status === 'resolved';

  return (
    <div className={`status-incident-card ${isResolved ? 'status-incident-resolved' : ''}`}>
      <div className="status-incident-header" onClick={onToggle} style={{ cursor: 'pointer' }}>
        <div className="status-incident-meta">
          <span className={`status-severity-badge ${sev.className}`}>{sev.label}</span>
          <span className="status-status-badge">{STATUS_LABELS[incident.status] || incident.status}</span>
        </div>
        <h4 className="status-incident-title">{incident.title}</h4>
        <div className="status-incident-time">{formatDate(incident.started_at)}</div>
      </div>

      {expanded && (
        <div className="status-incident-body">
          {incident.body && <p className="status-incident-desc">{incident.body}</p>}

          {incident.scheduled_end && (
            <p className="status-incident-scheduled">
              Scheduled end: {formatDate(incident.scheduled_end)}
            </p>
          )}

          {incident.updates && incident.updates.length > 0 && (
            <div className="status-timeline">
              {incident.updates.map((u) => (
                <div key={u.id} className="status-timeline-entry">
                  <div className="status-timeline-dot" />
                  <div className="status-timeline-content">
                    <div className="status-timeline-meta">
                      <span className="status-status-badge status-status-badge-sm">{STATUS_LABELS[u.status]}</span>
                      <span className="status-timeline-time">{formatDate(u.created_at)}</span>
                    </div>
                    <p className="status-timeline-body">{u.body}</p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
