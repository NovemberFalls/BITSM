import { useEffect, useState } from 'react';
import { useNotificationStore } from '../../store/notificationStore';
import type { NotificationPreference } from '../../store/notificationStore';
import type { GroupEventMatrixEntry, TeamEventMatrixEntry, NotificationTemplate } from '../../types';
import { api } from '../../api/client';

// All distinct notification events (alphabetical, prefixed to group related events)
const ALL_MATRIX_EVENTS = [
  { event: 'category_changed',  label: 'Category Changed' },
  { event: 'priority_changed',  label: 'Priority Changed' },
  { event: 'agent_reply',       label: 'Reply — Agent' },
  { event: 'internal_note',     label: 'Reply — Internal Note' },
  { event: 'requester_reply',   label: 'Reply — Requester' },
  { event: 'sla_breach',        label: 'SLA Breach' },
  { event: 'sla_warning',       label: 'SLA Warning' },
  { event: 'team_assigned',     label: 'Team Assigned' },
  { event: 'ticket_assigned',   label: 'Ticket Assigned' },
  { event: 'ticket_closed',     label: 'Ticket Closed' },
  { event: 'ticket_created',    label: 'Ticket Created' },
  { event: 'ticket_resolved',   label: 'Ticket Resolved' },
  { event: 'status_changed',    label: 'Ticket Status Changed' },
];

// Which events apply to each recipient type
const REQUESTER_EVENTS = new Set([
  'ticket_created', 'ticket_resolved', 'ticket_closed', 'status_changed',
  'priority_changed', 'agent_reply',
]);
const AGENT_EVENTS = new Set([
  'ticket_created', 'ticket_assigned', 'team_assigned', 'status_changed',
  'priority_changed', 'category_changed', 'requester_reply', 'internal_note',
  'sla_warning', 'sla_breach',
]);
const GROUP_EVENTS = new Set([
  'ticket_created', 'ticket_assigned', 'team_assigned', 'ticket_resolved', 'ticket_closed',
  'status_changed', 'priority_changed', 'category_changed', 'internal_note',
  'sla_warning', 'sla_breach',
]);

export function NotificationManager() {
  const {
    groups, activeGroupMembers, activeGroupId, settings, preferences, loading,
    loadGroups, createGroup, deleteGroup, loadMembers, addMember, removeMember,
    loadSettings, updateSettings, loadPreferences, updatePreference,
  } = useNotificationStore();

  const [users, setUsers] = useState<any[]>([]);
  const [newGroupName, setNewGroupName] = useState('');
  const [newGroupDesc, setNewGroupDesc] = useState('');
  const [showCreateGroup, setShowCreateGroup] = useState(false);
  const [newBlocklistEntry, setNewBlocklistEntry] = useState('');
  const [addMemberUserId, setAddMemberUserId] = useState('');
  const [addExternalEmail, setAddExternalEmail] = useState('');
  const [section, setSection] = useState<'groups' | 'email_notifications' | 'templates' | 'settings'>('groups');

  // Group × event matrix state
  const [matrix, setMatrix] = useState<GroupEventMatrixEntry[]>([]);
  const [teamMatrix, setTeamMatrix] = useState<TeamEventMatrixEntry[]>([]);
  const [matrixLoading, setMatrixLoading] = useState(false);
  const [matrixSaving, setMatrixSaving] = useState(false);
  const [matrixSaved, setMatrixSaved] = useState(false);
  const [matrixError, setMatrixError] = useState<string | null>(null);

  // Template editor state
  const [templates, setTemplates] = useState<NotificationTemplate[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(false);
  const [selectedTemplateEvent, setSelectedTemplateEvent] = useState<string>('ticket_created');
  const [templateEdits, setTemplateEdits] = useState<Record<string, Partial<NotificationTemplate>>>({});
  const [templateSaving, setTemplateSaving] = useState<string | null>(null);
  const [templateSaved, setTemplateSaved] = useState<string | null>(null);

  useEffect(() => {
    loadGroups();
    loadSettings();
    loadPreferences();
    api.listUsers().then(setUsers).catch(() => {});
  }, []);

  useEffect(() => {
    if (section === 'email_notifications') {
      setMatrixLoading(true);
      setMatrixError(null);
      Promise.all([api.getGroupEventMatrix(), api.getTeamEventMatrix()])
        .then(([groupData, teamData]) => { setMatrix(groupData); setTeamMatrix(teamData); })
        .catch(() => setMatrixError('Failed to load event matrix.'))
        .finally(() => setMatrixLoading(false));
    }
    if (section === 'templates') {
      setTemplatesLoading(true);
      api.getNotificationTemplates()
        .then((data) => { setTemplates(data); setTemplateEdits({}); })
        .catch(() => {})
        .finally(() => setTemplatesLoading(false));
    }
  }, [section]);

  const handleMatrixToggle = (groupId: number, eventName: string, enabled: boolean) => {
    setMatrix((prev) =>
      prev.map((entry) => {
        if (entry.group_id !== groupId) return entry;
        return {
          ...entry,
          events: entry.events.map((ev) =>
            ev.event === eventName ? { ...ev, enabled } : ev
          ),
        };
      })
    );
  };

  const handleTeamMatrixToggle = (teamId: number, eventName: string, enabled: boolean) => {
    setTeamMatrix((prev) =>
      prev.map((entry) => {
        if (entry.team_id !== teamId) return entry;
        return {
          ...entry,
          events: entry.events.map((ev) =>
            ev.event === eventName ? { ...ev, enabled } : ev
          ),
        };
      })
    );
  };

  const handleMatrixSaveAll = async () => {
    setMatrixSaving(true);
    setMatrixSaved(false);
    setMatrixError(null);
    try {
      await Promise.all([
        ...matrix.map((entry) => api.updateGroupEvents(entry.group_id, entry.events)),
        ...teamMatrix.map((entry) => api.updateTeamEvents(entry.team_id, entry.events)),
      ]);
      setMatrixSaved(true);
      setTimeout(() => setMatrixSaved(false), 3000);
    } catch {
      setMatrixError('Failed to save event subscriptions. Please try again.');
    } finally {
      setMatrixSaving(false);
    }
  };

  const handleCreateGroup = async () => {
    if (!newGroupName.trim()) return;
    await createGroup(newGroupName.trim(), newGroupDesc.trim());
    setNewGroupName('');
    setNewGroupDesc('');
    setShowCreateGroup(false);
  };

  const handleAddBlocklistEntry = async () => {
    if (!newBlocklistEntry.trim()) return;
    const updated = [...settings.email_blocklist, newBlocklistEntry.trim()];
    await updateSettings({ email_blocklist: updated });
    setNewBlocklistEntry('');
  };

  const handleRemoveBlocklistEntry = async (idx: number) => {
    const updated = settings.email_blocklist.filter((_, i) => i !== idx);
    await updateSettings({ email_blocklist: updated });
  };

  const handleAddMember = async () => {
    if (!addMemberUserId || !activeGroupId) return;
    await addMember(activeGroupId, { user_id: parseInt(addMemberUserId) });
    setAddMemberUserId('');
  };

  const handleAddExternalEmail = async () => {
    if (!addExternalEmail.trim() || !activeGroupId) return;
    await addMember(activeGroupId, { email: addExternalEmail.trim() });
    setAddExternalEmail('');
  };

  return (
    <div>
      {/* Section tabs */}
      <div className="comment-tabs" style={{ marginBottom: 20 }}>
        <button className={`comment-tab ${section === 'groups' ? 'active' : ''}`} onClick={() => setSection('groups')}>
          Groups
        </button>
        <button className={`comment-tab ${section === 'email_notifications' ? 'active' : ''}`} onClick={() => setSection('email_notifications')}>
          Email Notifications
        </button>
        <button className={`comment-tab ${section === 'templates' ? 'active' : ''}`} onClick={() => setSection('templates')}>
          Templates
        </button>
        <button className={`comment-tab ${section === 'settings' ? 'active' : ''}`} onClick={() => setSection('settings')}>
          Settings
        </button>
      </div>

      {/* Groups */}
      {section === 'groups' && (
        <div className="notif-section">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
            <h4 className="notif-section-title" style={{ margin: 0 }}>Notification Groups</h4>
            <button className="btn btn-sm btn-primary" onClick={() => setShowCreateGroup(!showCreateGroup)}>
              {showCreateGroup ? 'Cancel' : '+ New Group'}
            </button>
          </div>

          {showCreateGroup && (
            <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
              <input
                className="form-input"
                value={newGroupName}
                onChange={(e) => setNewGroupName(e.target.value)}
                placeholder="Group name"
                style={{ flex: 1 }}
              />
              <input
                className="form-input"
                value={newGroupDesc}
                onChange={(e) => setNewGroupDesc(e.target.value)}
                placeholder="Description (optional)"
                style={{ flex: 2 }}
              />
              <button className="btn btn-sm btn-primary" onClick={handleCreateGroup} disabled={!newGroupName.trim()}>
                Create
              </button>
            </div>
          )}

          {loading && groups.length === 0 ? (
            <div style={{ color: 'var(--t-text-muted)', padding: 16 }}>Loading...</div>
          ) : groups.length === 0 ? (
            <div style={{ color: 'var(--t-text-muted)', padding: 16 }}>No notification groups yet.</div>
          ) : (
            <div className="notif-group-list">
              {groups.map((g) => (
                <div key={g.id}>
                  <div className="notif-group-card">
                    <div>
                      <div className="notif-group-name">{g.name}</div>
                      {g.description && <div className="notif-group-desc">{g.description}</div>}
                      <div className="notif-group-desc">{g.member_count} member{g.member_count !== 1 ? 's' : ''}</div>
                    </div>
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button
                        className="btn btn-xs btn-ghost"
                        onClick={() => loadMembers(g.id)}
                      >
                        {activeGroupId === g.id ? 'Hide' : 'Members'}
                      </button>
                      <button
                        className="btn btn-xs btn-ghost"
                        style={{ color: 'var(--t-error)' }}
                        onClick={() => deleteGroup(g.id)}
                      >
                        Delete
                      </button>
                    </div>
                  </div>

                  {/* Members panel */}
                  {activeGroupId === g.id && (
                    <div style={{ padding: '12px 16px', background: 'var(--t-input-bg)', borderRadius: 'var(--radius-xs)', marginTop: 4 }}>
                      <div className="notif-member-list">
                        {activeGroupMembers.map((m) => (
                          <span key={m.id} className="notif-member-chip">
                            {m.type === 'external' ? (
                              <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                                <span style={{ fontSize: 9, padding: '0 4px', background: 'rgba(255,180,0,0.15)', color: 'var(--t-warning)', borderRadius: 3 }}>EXT</span>
                                {m.email}
                              </span>
                            ) : m.name}
                            <span className="notif-member-remove" onClick={() => removeMember(g.id, m.id)}>x</span>
                          </span>
                        ))}
                        {activeGroupMembers.length === 0 && (
                          <span style={{ fontSize: 12, color: 'var(--t-text-dim)' }}>No members</span>
                        )}
                      </div>
                      {/* Add user member */}
                      <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
                        <select
                          className="form-input form-select"
                          value={addMemberUserId}
                          onChange={(e) => setAddMemberUserId(e.target.value)}
                          style={{ flex: 1 }}
                        >
                          <option value="">Add user...</option>
                          {users
                            .filter((u) => !activeGroupMembers.some((m) => m.user_id === u.id))
                            .map((u) => (
                              <option key={u.id} value={u.id}>{u.name} ({u.email})</option>
                            ))}
                        </select>
                        <button className="btn btn-sm btn-primary" onClick={handleAddMember} disabled={!addMemberUserId}>
                          Add
                        </button>
                      </div>
                      {/* Add external email */}
                      <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                        <input
                          className="form-input"
                          value={addExternalEmail}
                          onChange={(e) => setAddExternalEmail(e.target.value)}
                          placeholder="External email address..."
                          style={{ flex: 1, fontSize: 12 }}
                          onKeyDown={(e) => { if (e.key === 'Enter') handleAddExternalEmail(); }}
                        />
                        <button className="btn btn-sm btn-ghost" onClick={handleAddExternalEmail} disabled={!addExternalEmail.trim()}>
                          Add Email
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Email Notifications — unified events × recipients matrix */}
      {section === 'email_notifications' && (
        <div className="notif-section">
          <h4 className="notif-section-title" style={{ marginBottom: 4 }}>Email Notifications</h4>
          <p style={{ fontSize: 12, color: 'var(--t-text-muted)', margin: '0 0 16px' }}>
            Configure which ticket participants and groups receive email for each event. Participant changes save immediately. Group and Team changes require Save.
          </p>

          {matrixLoading ? (
            <div style={{ color: 'var(--t-text-muted)', padding: 16, fontSize: 13 }}>Loading…</div>
          ) : (
            <>
              <div style={{ background: 'var(--t-input-bg)', borderRadius: 'var(--radius-xs)', overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }} aria-label="Email notification matrix">
                  <thead>
                    {/* Group header row — categorizes columns */}
                    <tr>
                      <th rowSpan={2} style={{ textAlign: 'left', padding: '10px 16px', fontWeight: 600, color: 'var(--t-text-dim)', borderBottom: '1px solid var(--t-border-subtle, rgba(255,255,255,0.06))', whiteSpace: 'nowrap', minWidth: 160, verticalAlign: 'bottom' }}>
                        Event
                      </th>
                      <th colSpan={2} style={{
                        textAlign: 'center', padding: '6px 14px 2px', fontSize: 10, fontWeight: 700,
                        textTransform: 'uppercase', letterSpacing: '0.06em',
                        color: 'var(--t-text-muted)',
                        borderBottom: '1px solid var(--t-border-subtle, rgba(255,255,255,0.06))',
                        borderLeft: '1px solid var(--t-border-subtle, rgba(255,255,255,0.06))',
                      }}>
                        Ticket Participants
                      </th>
                      {matrix.length > 0 && (
                        <th colSpan={matrix.length} style={{
                          textAlign: 'center', padding: '6px 14px 2px', fontSize: 10, fontWeight: 700,
                          textTransform: 'uppercase', letterSpacing: '0.06em',
                          color: 'var(--t-text-muted)',
                          borderBottom: '1px solid var(--t-border-subtle, rgba(255,255,255,0.06))',
                          borderLeft: '1px solid var(--t-border-subtle, rgba(255,255,255,0.06))',
                        }}>
                          Notification Groups
                        </th>
                      )}
                      {teamMatrix.length > 0 && (
                        <th colSpan={teamMatrix.length} style={{
                          textAlign: 'center', padding: '6px 14px 2px', fontSize: 10, fontWeight: 700,
                          textTransform: 'uppercase', letterSpacing: '0.06em',
                          color: 'var(--t-text-muted)',
                          borderBottom: '1px solid var(--t-border-subtle, rgba(255,255,255,0.06))',
                          borderLeft: '1px solid var(--t-border-subtle, rgba(255,255,255,0.06))',
                        }}>
                          Teams
                        </th>
                      )}
                    </tr>
                    {/* Individual column names */}
                    <tr>
                      <th style={{ textAlign: 'center', padding: '6px 14px 10px', fontWeight: 600, color: 'var(--t-text-dim)', borderBottom: '1px solid var(--t-border-subtle, rgba(255,255,255,0.06))', borderLeft: '1px solid var(--t-border-subtle, rgba(255,255,255,0.06))', whiteSpace: 'nowrap' }}>
                        Requester
                      </th>
                      <th style={{ textAlign: 'center', padding: '6px 14px 10px', fontWeight: 600, color: 'var(--t-text-dim)', borderBottom: '1px solid var(--t-border-subtle, rgba(255,255,255,0.06))', whiteSpace: 'nowrap' }}>
                        Agent
                      </th>
                      {matrix.map((g, i) => (
                        <th key={`g-${g.group_id}`} style={{ textAlign: 'center', padding: '6px 14px 10px', fontWeight: 600, color: 'var(--t-text-dim)', borderBottom: '1px solid var(--t-border-subtle, rgba(255,255,255,0.06))', whiteSpace: 'nowrap', ...(i === 0 ? { borderLeft: '1px solid var(--t-border-subtle, rgba(255,255,255,0.06))' } : {}) }}>
                          {g.group_name}
                        </th>
                      ))}
                      {teamMatrix.map((t, i) => (
                        <th key={`t-${t.team_id}`} style={{ textAlign: 'center', padding: '6px 14px 10px', fontWeight: 600, color: 'var(--t-text-dim)', borderBottom: '1px solid var(--t-border-subtle, rgba(255,255,255,0.06))', whiteSpace: 'nowrap', ...(i === 0 ? { borderLeft: '1px solid var(--t-border-subtle, rgba(255,255,255,0.06))' } : {}) }}>
                          {t.team_name}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {ALL_MATRIX_EVENTS.map(({ event, label }) => (
                      <tr key={event}>
                        <td style={{ padding: '9px 16px', borderBottom: '1px solid var(--t-border-subtle, rgba(255,255,255,0.06))', color: 'var(--t-text)', whiteSpace: 'nowrap' }}>
                          {label}
                        </td>

                        {/* Requester */}
                        <td style={{ textAlign: 'center', padding: '9px 14px', borderBottom: '1px solid var(--t-border-subtle, rgba(255,255,255,0.06))' }}>
                          {REQUESTER_EVENTS.has(event) ? (
                            <input
                              type="checkbox"
                              checked={preferences.find((p) => p.event === event && p.role_target === 'requester' && p.channel === 'email')?.enabled ?? true}
                              onChange={(e) => updatePreference(event, 'email', 'requester', e.target.checked)}
                              style={{ accentColor: 'var(--t-accent, #4f8cff)', cursor: 'pointer' }}
                              aria-label={`Requester — ${label}`}
                            />
                          ) : (
                            <span style={{ color: 'var(--t-text-dim)', fontSize: 16 }}>—</span>
                          )}
                        </td>

                        {/* Agent */}
                        <td style={{ textAlign: 'center', padding: '9px 14px', borderBottom: '1px solid var(--t-border-subtle, rgba(255,255,255,0.06))' }}>
                          {AGENT_EVENTS.has(event) ? (
                            <input
                              type="checkbox"
                              checked={preferences.find((p) => p.event === event && p.role_target === 'assignee' && p.channel === 'email')?.enabled ?? true}
                              onChange={(e) => updatePreference(event, 'email', 'assignee', e.target.checked)}
                              style={{ accentColor: 'var(--t-accent, #4f8cff)', cursor: 'pointer' }}
                              aria-label={`Agent — ${label}`}
                            />
                          ) : (
                            <span style={{ color: 'var(--t-text-dim)', fontSize: 16 }}>—</span>
                          )}
                        </td>

                        {/* Groups */}
                        {matrix.map((entry) => (
                          <td key={`g-${entry.group_id}`} style={{ textAlign: 'center', padding: '9px 14px', borderBottom: '1px solid var(--t-border-subtle, rgba(255,255,255,0.06))' }}>
                            {GROUP_EVENTS.has(event) ? (
                              <input
                                type="checkbox"
                                checked={entry.events.find((ev) => ev.event === event && ev.channel === 'email')?.enabled ?? true}
                                onChange={(e) => handleMatrixToggle(entry.group_id, event, e.target.checked)}
                                style={{ accentColor: 'var(--t-accent, #4f8cff)', cursor: 'pointer' }}
                                aria-label={`${entry.group_name} — ${label}`}
                              />
                            ) : (
                              <span style={{ color: 'var(--t-text-dim)', fontSize: 16 }}>—</span>
                            )}
                          </td>
                        ))}

                        {/* Teams */}
                        {teamMatrix.map((entry) => (
                          <td key={`t-${entry.team_id}`} style={{ textAlign: 'center', padding: '9px 14px', borderBottom: '1px solid var(--t-border-subtle, rgba(255,255,255,0.06))' }}>
                            {GROUP_EVENTS.has(event) ? (
                              <input
                                type="checkbox"
                                checked={entry.events.find((ev) => ev.event === event && ev.channel === 'email')?.enabled ?? true}
                                onChange={(e) => handleTeamMatrixToggle(entry.team_id, event, e.target.checked)}
                                style={{ accentColor: 'var(--t-accent, #4f8cff)', cursor: 'pointer' }}
                                aria-label={`${entry.team_name} — ${label}`}
                              />
                            ) : (
                              <span style={{ color: 'var(--t-text-dim)', fontSize: 16 }}>—</span>
                            )}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Save group + team changes */}
              {(matrix.length > 0 || teamMatrix.length > 0) && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 0' }}>
                  <button className="btn btn-sm btn-primary" onClick={handleMatrixSaveAll} disabled={matrixSaving} aria-busy={matrixSaving}>
                    {matrixSaving ? 'Saving…' : 'Save Settings'}
                  </button>
                  {matrixSaved && <span style={{ fontSize: 12, color: 'var(--t-success, #4caf50)' }}>Saved</span>}
                  {matrixError && <span style={{ fontSize: 12, color: 'var(--t-error)' }}>{matrixError}</span>}
                </div>
              )}
              {matrix.length === 0 && teamMatrix.length === 0 && (
                <div style={{ fontSize: 12, color: 'var(--t-text-muted)', marginTop: 8 }}>
                  No notification groups or teams configured. Create groups in the Groups tab or teams in Admin to enable additional columns.
                </div>
              )}

              {/* Email config info */}
              <div style={{ background: 'var(--t-input-bg)', borderRadius: 'var(--radius-xs)', padding: 16, marginTop: 20 }}>
                <h4 className="notif-section-title" style={{ marginBottom: 8 }}>Email Configuration</h4>
                <div style={{ fontSize: 13, color: 'var(--t-text-muted)' }}>
                  <div style={{ marginBottom: 4 }}>
                    <span style={{ color: 'var(--t-text-dim)' }}>From address:</span>{' '}
                    <span style={{ fontFamily: 'var(--mono)', fontSize: 12 }}>Configured per tenant by super admin</span>
                  </div>
                  <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6, marginTop: 8, padding: '4px 10px', background: 'rgba(79,140,255,0.08)', borderRadius: 4, fontSize: 11 }}>
                    <span style={{ color: 'var(--t-accent, #4f8cff)' }}>Powered by Resend</span>
                  </div>
                </div>
              </div>
            </>
          )}
        </div>
      )}

      {/* Template Editor */}
      {section === 'templates' && (
        <div className="notif-section">
          <div style={{ marginBottom: 20 }}>
            <select
              className="form-input form-select"
              value={selectedTemplateEvent}
              onChange={(e) => setSelectedTemplateEvent(e.target.value)}
              style={{ minWidth: 240 }}
            >
              {[...templates]
                .map((t) => ({ ...t, label: ALL_MATRIX_EVENTS.find((e) => e.event === t.event)?.label || t.event.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()) }))
                .sort((a, b) => a.label.localeCompare(b.label))
                .map((t) => (
                <option key={t.event} value={t.event}>
                  {t.label}
                  {t.is_custom ? ' ✎' : ''}
                </option>
              ))}
            </select>
          </div>

          {templatesLoading ? (
            <div style={{ color: 'var(--t-text-muted)', fontSize: 13 }}>Loading…</div>
          ) : (() => {
            const tpl = templates.find((t) => t.event === selectedTemplateEvent);
            if (!tpl) return null;

            const edit = templateEdits[tpl.event] || {};
            const subject  = edit.subject_template ?? tpl.subject_template;
            const headline = edit.body_headline     ?? tpl.body_headline;
            const intro    = edit.body_intro        ?? tpl.body_intro;
            const isDirty  = subject !== tpl.subject_template || headline !== tpl.body_headline || intro !== tpl.body_intro;

            const setField = (field: string, val: string) =>
              setTemplateEdits((prev) => ({ ...prev, [tpl.event]: { ...(prev[tpl.event] || {}), [field]: val } }));

            const save = async () => {
              setTemplateSaving(tpl.event);
              try {
                await api.updateNotificationTemplate(tpl.event, { subject_template: subject, body_headline: headline, body_intro: intro });
                setTemplates((prev) => prev.map((t) => t.event === tpl.event
                  ? { ...t, is_custom: true, subject_template: subject, body_headline: headline, body_intro: intro }
                  : t));
                setTemplateEdits((prev) => { const n = { ...prev }; delete n[tpl.event]; return n; });
                setTemplateSaved(tpl.event);
                setTimeout(() => setTemplateSaved(null), 2500);
              } catch { /* silent */ }
              setTemplateSaving(null);
            };

            const reset = async () => {
              await api.resetNotificationTemplate(tpl.event);
              setTemplates((prev) => prev.map((t) => t.event === tpl.event
                ? { ...t, is_custom: false, subject_template: t.default_subject, body_headline: t.default_headline, body_intro: t.default_intro }
                : t));
              setTemplateEdits((prev) => { const n = { ...prev }; delete n[tpl.event]; return n; });
            };

            const VAR_DESCRIPTIONS: Record<string, string> = {
              ticket_number:   'Case number e.g. TKT-001',
              subject:         'Ticket title / summary line',
              description:     'Full ticket description (truncated to 300 chars)',
              status:          'Current ticket status',
              old_status:      'Status before the change',
              new_status:      'Status after the change',
              priority:        'Priority level e.g. P2 — High',
              category:        'Problem category assigned to the ticket',
              tags:            'Comma-separated list of ticket tags',
              requester_name:  'Name of the person who filed the ticket',
              requester_email: 'Email address of the requester',
              assignee_name:   'Name of the assigned agent (or "Unassigned")',
              author_name:     'Name of the comment author',
              time_remaining:  'Time left before SLA deadline is breached',
              created_date:    'Date the ticket was opened e.g. March 24, 2026',
              ticket_url:      'Direct link to view the ticket',
              tenant_name:     'Name of your organisation / tenant',
              app_name:        'Name of the helpdesk platform',
            };

            const eventLabel = ALL_MATRIX_EVENTS.find(e => e.event === tpl.event)?.label ?? tpl.event.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

            return (
              <div className="card" style={{ padding: 20 }}>
                {/* Card title */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 20 }}>
                  <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: 'var(--t-text)' }}>{eventLabel}</h3>
                  {tpl.is_custom && !isDirty && (
                    <span style={{ fontSize: 11, padding: '2px 8px', background: 'rgba(79,140,255,0.12)', color: 'var(--t-accent, #4f8cff)', borderRadius: 10, border: '1px solid rgba(79,140,255,0.25)' }}>Customised</span>
                  )}
                  {isDirty && (
                    <span style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>Unsaved changes</span>
                  )}
                </div>

                {/* Two-column body */}
                <div style={{ display: 'flex', gap: 24, alignItems: 'flex-start' }}>
                  {/* Left: form fields */}
                  <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 14 }}>
                    <div>
                      <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--t-text-dim)', display: 'block', marginBottom: 4 }}>SUBJECT LINE</label>
                      <input
                        className="form-input"
                        value={subject}
                        onChange={(e) => setField('subject_template', e.target.value)}
                        style={{ width: '100%', fontFamily: 'var(--mono)', fontSize: 12 }}
                      />
                    </div>
                    <div>
                      <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--t-text-dim)', display: 'block', marginBottom: 4 }}>EMAIL HEADLINE</label>
                      <input
                        className="form-input"
                        value={headline}
                        onChange={(e) => setField('body_headline', e.target.value)}
                        style={{ width: '100%', fontSize: 14 }}
                      />
                    </div>
                    <div>
                      <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--t-text-dim)', display: 'block', marginBottom: 4 }}>INTRO PARAGRAPH</label>
                      <textarea
                        className="form-input"
                        value={intro}
                        onChange={(e) => setField('body_intro', e.target.value)}
                        rows={4}
                        style={{ width: '100%', fontSize: 13, resize: 'vertical' }}
                      />
                    </div>
                  </div>

                  {/* Right: variable reference */}
                  <div style={{ width: 420, flexShrink: 0, display: 'flex', flexDirection: 'column' }}>
                    <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--t-text-dim)', marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.5 }}>
                      Variables — click to copy
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
                      {Object.keys(VAR_DESCRIPTIONS).sort().map((v) => (
                        <div
                          key={v}
                          onClick={() => navigator.clipboard?.writeText(`{{${v}}}`)}
                          title="Click to copy"
                          style={{ cursor: 'pointer', borderRadius: 6, padding: '6px 10px', border: '1px solid var(--t-border, rgba(100,100,100,0.3))', background: 'var(--t-input-bg)', transition: 'border-color 0.15s' }}
                          onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'var(--t-accent, #4f8cff)')}
                          onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--t-border, rgba(100,100,100,0.3))')}
                        >
                          <div style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--t-accent, #4f8cff)', fontWeight: 600, marginBottom: 2 }}>
                            {`{{${v}}}`}
                          </div>
                          <div style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>
                            {VAR_DESCRIPTIONS[v] || v}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>

                {/* Footer actions */}
                <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 8, marginTop: 20, paddingTop: 16, borderTop: '1px solid var(--t-border, rgba(100,100,100,0.2))' }}>
                  {templateSaved === tpl.event && <span style={{ fontSize: 12, color: 'var(--t-success)' }}>Saved</span>}
                  {tpl.is_custom && (
                    <button className="btn btn-sm btn-ghost" onClick={reset}>Reset to default</button>
                  )}
                  <button className="btn btn-sm btn-primary" onClick={save} disabled={!isDirty || templateSaving === tpl.event}>
                    {templateSaving === tpl.event ? 'Saving…' : 'Save'}
                  </button>
                </div>
              </div>
            );
          })()}
        </div>
      )}

      {/* Settings */}
      {section === 'settings' && (
        <div className="notif-section">
          {/* Anti-loop blocklist */}
          <div style={{ marginBottom: 28 }}>
            <h4 className="notif-section-title">Email Anti-Loop Protection</h4>
            <p style={{ fontSize: 12, color: 'var(--t-text-muted)', marginBottom: 16 }}>
              Emails matching these patterns will never be sent to prevent notification loops with other ticketing systems.
            </p>

            <label className="comment-email-toggle" style={{ marginBottom: 16 }}>
              <input
                type="checkbox"
                checked={settings.email_loop_detection}
                onChange={(e) => updateSettings({ email_loop_detection: e.target.checked })}
              />
              <span>Enable loop detection (block known ticketing system emails)</span>
            </label>

            <div className="notif-blocklist">
              {settings.email_blocklist.map((pattern, idx) => (
                <div key={idx} className="notif-blocklist-item">
                  <span>{pattern}</span>
                  <span
                    className="notif-member-remove"
                    onClick={() => handleRemoveBlocklistEntry(idx)}
                  >x</span>
                </div>
              ))}
              {settings.email_blocklist.length === 0 && (
                <div style={{ fontSize: 12, color: 'var(--t-text-dim)', padding: 8 }}>No custom blocklist entries.</div>
              )}
            </div>

            <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
              <input
                className="form-input"
                value={newBlocklistEntry}
                onChange={(e) => setNewBlocklistEntry(e.target.value)}
                placeholder="*@noreply.example.com"
                style={{ flex: 1, fontFamily: 'var(--mono)', fontSize: 12 }}
              />
              <button className="btn btn-sm btn-primary" onClick={handleAddBlocklistEntry} disabled={!newBlocklistEntry.trim()}>
                Add Pattern
              </button>
            </div>
          </div>

          {/* Inbound email */}
          <div style={{ marginBottom: 28 }}>
            <h4 className="notif-section-title">Inbound Email — Email to Ticket</h4>
            <p style={{ fontSize: 12, color: 'var(--t-text-muted)', marginBottom: 16 }}>
              Emails sent to your inbound address automatically create support tickets. Replies that include the ticket number (e.g. <code style={{ fontFamily: 'var(--mono)', fontSize: 11 }}>[TKT-00001]</code>) are added as comments instead.
            </p>
            {(() => {
              const slug = (window as any).__APP_CONFIG__?.tenant_slug;
              const domain = (window as any).__APP_CONFIG__?.tenant_settings?.inbound_email_domain || 'bitsm.io';
              if (!slug) return (
                <div style={{ fontSize: 12, color: 'var(--t-text-muted)', fontStyle: 'italic' }}>
                  Tenant slug not available — contact your system administrator.
                </div>
              );
              const addr = `${slug}@${domain}`;
              return (
                <>
                  <div className="notif-group-card" style={{ alignItems: 'center' }}>
                    <div>
                      <div className="notif-group-name">Your inbound email address</div>
                      <code style={{ fontFamily: 'var(--mono)', fontSize: 13, color: 'var(--t-accent, #4f8cff)' }}>{addr}</code>
                    </div>
                    <button
                      className="btn btn-sm btn-ghost"
                      onClick={() => navigator.clipboard?.writeText(addr)}
                    >
                      Copy
                    </button>
                  </div>
                  <p style={{ fontSize: 11, color: 'var(--t-text-dim)', marginTop: 12 }}>
                    To activate: enable Cloudflare Email Routing on <code style={{ fontFamily: 'var(--mono)', fontSize: 11 }}>{domain}</code> with a catch-all rule pointing to the <strong>helpdesk-email-worker</strong> Worker.
                  </p>
                </>
              );
            })()}
          </div>

          {/* Channels — per-tenant webhook URLs */}
          <div>
            <h4 className="notif-section-title">Channels</h4>
            <p style={{ fontSize: 12, color: 'var(--t-text-muted)', margin: '0 0 16px' }}>
              Paste your incoming webhook URL below to receive ticket notifications. Each organisation has its own URL — no server config changes required.
            </p>

            {/* Teams */}
            <div style={{ background: 'var(--t-input-bg)', borderRadius: 'var(--radius-xs)', padding: 16, marginBottom: 12 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                <div>
                  <div className="notif-group-name">Microsoft Teams</div>
                  <div className="notif-group-desc">Adaptive Card notifications for ticket events</div>
                </div>
                <label className="comment-email-toggle">
                  <input
                    type="checkbox"
                    checked={settings.teams_webhook_enabled}
                    onChange={(e) => updateSettings({ teams_webhook_enabled: e.target.checked })}
                  />
                  <span>Enabled</span>
                </label>
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <input
                  className="form-input"
                  type="url"
                  value={settings.teams_webhook_url}
                  onChange={(e) => updateSettings({ teams_webhook_url: e.target.value })}
                  placeholder="https://outlook.office.com/webhook/..."
                  style={{ flex: 1, fontFamily: 'var(--mono)', fontSize: 12 }}
                />
              </div>
              {settings.teams_webhook_url && (
                <div style={{ fontSize: 11, color: 'var(--t-text-dim)', marginTop: 6 }}>
                  Notifications will be sent to this channel when tickets are created, updated, or breach SLA.
                </div>
              )}
            </div>

            {/* Slack */}
            <div style={{ background: 'var(--t-input-bg)', borderRadius: 'var(--radius-xs)', padding: 16 }}>
              <div style={{ marginBottom: 10 }}>
                <div className="notif-group-name">Slack</div>
                <div className="notif-group-desc">Block Kit notifications for ticket events</div>
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <input
                  className="form-input"
                  type="url"
                  value={settings.slack_webhook_url}
                  onChange={(e) => updateSettings({ slack_webhook_url: e.target.value })}
                  placeholder="https://hooks.slack.com/services/..."
                  style={{ flex: 1, fontFamily: 'var(--mono)', fontSize: 12 }}
                />
              </div>
              {settings.slack_webhook_url && (
                <div style={{ fontSize: 11, color: 'var(--t-text-dim)', marginTop: 6 }}>
                  Notifications will be sent to this channel when tickets are created, updated, or breach SLA.
                </div>
              )}
              <div style={{ fontSize: 11, color: 'var(--t-text-muted)', marginTop: 10 }}>
                In Slack: <strong>Apps → Incoming Webhooks → Add to Slack</strong> → choose channel → copy URL above.
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
