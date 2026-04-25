import { useEffect, useState } from 'react';
import { useAutomationStore } from '../../store/automationStore';
import { useUIStore } from '../../store/uiStore';
import { pushUrl } from '../../utils/url';

const TRIGGER_LABELS: Record<string, string> = {
  ticket_created: 'Ticket Created',
  status_changed: 'Status Changed',
  priority_changed: 'Priority Changed',
  comment_added: 'Comment Added',
  assignee_changed: 'Assignee Changed',
  tag_added: 'Tag Added',
  sla_breached: 'SLA Breached',
  schedule: 'Schedule',
};

export function AutomationList() {
  const { automations, loading, fetchAutomations, createAutomation, toggleAutomation, deleteAutomation } = useAutomationStore();
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [creating, setCreating] = useState(false);

  useEffect(() => { fetchAutomations(); }, []);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    setCreating(true);
    try {
      const id = await createAutomation({ name: newName, trigger_type: 'ticket_created' });
      setShowCreate(false);
      setNewName('');
      // Navigate to builder
      pushUrl(`/automations/${id}`);
      useUIStore.getState().setView('automations');
    } finally {
      setCreating(false);
    }
  };

  const openBuilder = (id: number) => {
    pushUrl(`/automations/${id}`);
    // Force re-render by setting view
    useUIStore.getState().setView('automations');
    // Trigger a re-render since pathToView doesn't change
    window.dispatchEvent(new PopStateEvent('popstate'));
  };

  const formatDate = (d: string | null) => {
    if (!d) return 'Never';
    return new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  };

  return (
    <div className="automation-list">
      <div className="automation-list-header">
        <div>
          <h2 className="automation-list-title">Automations</h2>
          <p className="automation-list-subtitle">Build visual workflows to automate ticket actions</p>
        </div>
        <button className="btn btn-primary" onClick={() => setShowCreate(true)}>
          + New Automation
        </button>
      </div>

      {showCreate && (
        <div className="automation-create-form">
          <div className="automation-create-fields">
            <input
              type="text"
              placeholder="Automation name..."
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
              autoFocus
              className="input"
            />
            <button className="btn btn-primary" onClick={handleCreate} disabled={creating || !newName.trim()}>
              {creating ? 'Creating...' : 'Create'}
            </button>
            <button className="btn btn-ghost" onClick={() => setShowCreate(false)}>Cancel</button>
          </div>
        </div>
      )}

      {loading && <div className="automation-loading">Loading automations...</div>}

      {!loading && automations.length === 0 && !showCreate && (
        <div className="automation-empty">
          <div className="automation-empty-icon">
            <svg width="48" height="48" viewBox="0 0 48 48" fill="none" stroke="var(--t-text-muted)" strokeWidth="1.5">
              <circle cx="12" cy="12" r="5" />
              <circle cx="36" cy="12" r="5" />
              <circle cx="24" cy="36" r="5" />
              <path d="M17 12h14M12 17v10l12 9M36 17v10l-12 9" />
            </svg>
          </div>
          <p>No automations yet. Create one to get started.</p>
        </div>
      )}

      <div className="automation-grid">
        {automations.map((a) => (
          <div key={a.id} className="automation-card" onClick={() => openBuilder(a.id)}>
            <div className="automation-card-header">
              <span className="automation-card-name">{a.name}</span>
              <button
                className={`automation-toggle ${a.is_active ? 'active' : ''}`}
                onClick={(e) => { e.stopPropagation(); toggleAutomation(a.id); }}
                title={a.is_active ? 'Deactivate' : 'Activate'}
              >
                <span className="automation-toggle-track">
                  <span className="automation-toggle-thumb" />
                </span>
              </button>
            </div>
            {a.description && <div className="automation-card-desc">{a.description}</div>}
            <div className="automation-card-meta">
              <span className="automation-card-trigger">{TRIGGER_LABELS[a.trigger_type] || a.trigger_type}</span>
              <span className="automation-card-stat">{a.run_count} runs</span>
              <span className="automation-card-stat">Last: {formatDate(a.last_run_at)}</span>
            </div>
            <div className="automation-card-footer">
              <span className="automation-card-date">Created {formatDate(a.created_at)}</span>
              <button
                className="btn btn-ghost btn-xs"
                onClick={(e) => {
                  e.stopPropagation();
                  if (confirm('Delete this automation?')) deleteAutomation(a.id);
                }}
              >Delete</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
