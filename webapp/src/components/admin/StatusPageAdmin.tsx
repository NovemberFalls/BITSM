import { useEffect, useState } from 'react';
import { api } from '../../api/client';

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
}

const STATUSES = ['scheduled', 'investigating', 'identified', 'monitoring', 'resolved'] as const;
const SEVERITIES = ['minor', 'major', 'critical', 'maintenance'] as const;

const STATUS_LABELS: Record<string, string> = {
  scheduled: 'Scheduled', investigating: 'Investigating',
  identified: 'Identified', monitoring: 'Monitoring', resolved: 'Resolved',
};
const SEVERITY_LABELS: Record<string, string> = {
  minor: 'Minor', major: 'Major', critical: 'Critical', maintenance: 'Maintenance',
};

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', {
    month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}

export function StatusPageAdmin() {
  const [incidents, setIncidents] = useState<StatusIncident[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);
  const [updateIncidentId, setUpdateIncidentId] = useState<number | null>(null);
  const [updateBody, setUpdateBody] = useState('');
  const [updateStatus, setUpdateStatus] = useState('investigating');

  // Form state
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [status, setStatus] = useState<string>('investigating');
  const [severity, setSeverity] = useState<string>('minor');
  const [scheduledEnd, setScheduledEnd] = useState('');

  const load = () => {
    api.listStatusIncidents()
      .then(setIncidents)
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const resetForm = () => {
    setTitle(''); setBody(''); setStatus('investigating');
    setSeverity('minor'); setScheduledEnd('');
    setEditId(null); setShowForm(false);
  };

  const handleEdit = (i: StatusIncident) => {
    setTitle(i.title);
    setBody(i.body);
    setStatus(i.status);
    setSeverity(i.severity);
    setScheduledEnd(i.scheduled_end ? i.scheduled_end.slice(0, 16) : '');
    setEditId(i.id);
    setShowForm(true);
  };

  const handleSave = async () => {
    const data: any = { title, body, status, severity };
    if (scheduledEnd) data.scheduled_end = new Date(scheduledEnd).toISOString();
    else data.scheduled_end = null;

    if (editId) {
      await api.updateStatusIncident(editId, data);
    } else {
      await api.createStatusIncident(data);
    }
    resetForm();
    load();
  };

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this incident?')) return;
    await api.deleteStatusIncident(id);
    load();
  };

  const handleAddUpdate = async () => {
    if (!updateIncidentId || !updateBody.trim()) return;
    await api.addStatusUpdate(updateIncidentId, { body: updateBody, status: updateStatus });
    setUpdateIncidentId(null);
    setUpdateBody('');
    setUpdateStatus('investigating');
    load();
  };

  const active = incidents.filter((i) => i.status !== 'resolved');
  const resolved = incidents.filter((i) => i.status === 'resolved');

  return (
    <div className="admin-section">
      <div className="admin-section-header">
        <h3>Status Page</h3>
        <button className="btn btn-primary btn-sm" onClick={() => { resetForm(); setShowForm(true); }}>
          + New Incident
        </button>
      </div>
      <p className="admin-section-desc" style={{ color: 'var(--t-text-muted)', fontSize: 13, marginBottom: 16 }}>
        Manage planned outages, known issues, and maintenance windows visible on the customer portal.
      </p>

      {/* Create / Edit form */}
      {showForm && (
        <div className="card" style={{ padding: 16, marginBottom: 16, border: '1px solid var(--t-border)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
            <strong style={{ color: 'var(--t-text-bright)' }}>{editId ? 'Edit Incident' : 'New Incident'}</strong>
            <button className="btn btn-ghost btn-sm" onClick={resetForm}>Cancel</button>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <input
              className="form-input"
              placeholder="Incident title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
            <textarea
              className="form-input"
              placeholder="Description (optional)"
              rows={3}
              value={body}
              onChange={(e) => setBody(e.target.value)}
            />
            <div style={{ display: 'flex', gap: 10 }}>
              <select className="form-input" value={severity} onChange={(e) => setSeverity(e.target.value)} style={{ flex: 1 }}>
                {SEVERITIES.map((s) => <option key={s} value={s}>{SEVERITY_LABELS[s]}</option>)}
              </select>
              <select className="form-input" value={status} onChange={(e) => setStatus(e.target.value)} style={{ flex: 1 }}>
                {STATUSES.map((s) => <option key={s} value={s}>{STATUS_LABELS[s]}</option>)}
              </select>
            </div>
            {(severity === 'maintenance' || status === 'scheduled') && (
              <div>
                <label style={{ fontSize: 12, color: 'var(--t-text-muted)' }}>Scheduled End</label>
                <input
                  className="form-input"
                  type="datetime-local"
                  value={scheduledEnd}
                  onChange={(e) => setScheduledEnd(e.target.value)}
                />
              </div>
            )}
            <button className="btn btn-primary btn-sm" onClick={handleSave} disabled={!title.trim()}>
              {editId ? 'Save Changes' : 'Create Incident'}
            </button>
          </div>
        </div>
      )}

      {loading && <div style={{ color: 'var(--t-text-muted)', padding: 20 }}>Loading...</div>}

      {/* Active incidents */}
      {active.length > 0 && (
        <>
          <h4 style={{ color: 'var(--t-text-bright)', margin: '16px 0 8px' }}>Active ({active.length})</h4>
          {active.map((i) => (
            <IncidentRow
              key={i.id}
              incident={i}
              onEdit={() => handleEdit(i)}
              onDelete={() => handleDelete(i.id)}
              onAddUpdate={() => { setUpdateIncidentId(i.id); setUpdateStatus(i.status); }}
            />
          ))}
        </>
      )}

      {/* Resolved */}
      {resolved.length > 0 && (
        <>
          <h4 style={{ color: 'var(--t-text-muted)', margin: '16px 0 8px' }}>Resolved ({resolved.length})</h4>
          {resolved.map((i) => (
            <IncidentRow
              key={i.id}
              incident={i}
              onEdit={() => handleEdit(i)}
              onDelete={() => handleDelete(i.id)}
              onAddUpdate={() => { setUpdateIncidentId(i.id); setUpdateStatus(i.status); }}
            />
          ))}
        </>
      )}

      {!loading && incidents.length === 0 && (
        <div style={{ color: 'var(--t-text-muted)', padding: 20, textAlign: 'center' }}>
          No incidents. Create one to notify portal users about outages or maintenance.
        </div>
      )}

      {/* Add timeline update modal */}
      {updateIncidentId && (
        <div className="modal-overlay" onClick={() => setUpdateIncidentId(null)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 480 }}>
            <h4 style={{ color: 'var(--t-text-bright)', marginBottom: 12 }}>Post Status Update</h4>
            <textarea
              className="form-input"
              placeholder="What's the latest?"
              rows={3}
              value={updateBody}
              onChange={(e) => setUpdateBody(e.target.value)}
              autoFocus
            />
            <select
              className="form-input"
              value={updateStatus}
              onChange={(e) => setUpdateStatus(e.target.value)}
              style={{ marginTop: 8 }}
            >
              {STATUSES.map((s) => <option key={s} value={s}>{STATUS_LABELS[s]}</option>)}
            </select>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 12 }}>
              <button className="btn btn-ghost btn-sm" onClick={() => setUpdateIncidentId(null)}>Cancel</button>
              <button className="btn btn-primary btn-sm" onClick={handleAddUpdate} disabled={!updateBody.trim()}>
                Post Update
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function IncidentRow({ incident, onEdit, onDelete, onAddUpdate }: {
  incident: StatusIncident;
  onEdit: () => void;
  onDelete: () => void;
  onAddUpdate: () => void;
}) {
  const isResolved = incident.status === 'resolved';
  return (
    <div className="card" style={{
      padding: '12px 16px', marginBottom: 8,
      border: '1px solid var(--t-border)',
      opacity: isResolved ? 0.6 : 1,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
        <span className={`status-severity-badge status-severity-${incident.severity}`}>
          {SEVERITY_LABELS[incident.severity]}
        </span>
        <span className="status-status-badge">{STATUS_LABELS[incident.status]}</span>
        <span style={{ flex: 1 }} />
        <button className="btn btn-ghost btn-xs" onClick={onAddUpdate} title="Post update">Update</button>
        <button className="btn btn-ghost btn-xs" onClick={onEdit} title="Edit">Edit</button>
        <button className="btn btn-ghost btn-xs" onClick={onDelete} title="Delete" style={{ color: 'var(--t-danger)' }}>Delete</button>
      </div>
      <div style={{ fontWeight: 600, color: 'var(--t-text-bright)', fontSize: 14 }}>{incident.title}</div>
      {incident.body && <div style={{ color: 'var(--t-text-muted)', fontSize: 13, marginTop: 4 }}>{incident.body}</div>}
      <div style={{ color: 'var(--t-text-muted)', fontSize: 12, marginTop: 6 }}>
        Started {formatDate(incident.started_at)}
        {incident.resolved_at && <> &middot; Resolved {formatDate(incident.resolved_at)}</>}
        {incident.author_name && <> &middot; {incident.author_name}</>}
      </div>
    </div>
  );
}
