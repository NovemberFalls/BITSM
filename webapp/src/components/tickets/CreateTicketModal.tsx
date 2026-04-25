import { useState, useEffect } from 'react';
import { useUIStore } from '../../store/uiStore';
import { useTicketStore } from '../../store/ticketStore';
import { useHierarchyStore } from '../../store/hierarchyStore';
import { useAuthStore } from '../../store/authStore';
import { api } from '../../api/client';
import type { Agent } from '../../types';
import { CascadingSelect } from '../common/CascadingSelect';

export function CreateTicketModal() {
  const open = useUIStore((s) => s.createTicketOpen);
  const close = useUIStore((s) => s.setCreateTicketOpen);
  const createTicket = useTicketStore((s) => s.createTicket);
  const { locations, problemCategories, loadAll } = useHierarchyStore();
  const user = useAuthStore((s) => s.user);

  const [ticketType, setTicketType] = useState<'support' | 'task' | 'bug' | 'feature' | 'custom'>('support');
  const [subject, setSubject] = useState('');
  const [description, setDescription] = useState('');
  // Bug built-in fields
  const [stepsToReproduce, setStepsToReproduce] = useState('');
  const [expectedBehavior, setExpectedBehavior] = useState('');
  const [actualBehavior, setActualBehavior] = useState('');
  const [storyPoints, setStoryPoints] = useState<number | null>(null);
  const [sprintId, setSprintId] = useState<number | null>(null);
  const [sprints, setSprints] = useState<any[]>([]);
  const [locationId, setLocationId] = useState<number | null>(null);
  const [problemCategoryId, setProblemCategoryId] = useState<number | null>(null);
  const [requesterId, setRequesterId] = useState<number | null>(null);
  const [teamId, setTeamId] = useState<number | null>(null);
  const [teams, setTeams] = useState<any[]>([]);
  const [allUsers, setAllUsers] = useState<Agent[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [errorFields, setErrorFields] = useState<Set<string>>(new Set());

  // Custom fields
  const [customFields, setCustomFields] = useState<any[]>([]);
  const [customValues, setCustomValues] = useState<Record<string, any>>({});

  // Form templates (for Custom type catalog)
  const [templates, setTemplates] = useState<any[]>([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState<number | null>(null);

  const isStaff = user?.role && ['super_admin', 'tenant_admin', 'agent'].includes(user.role);

  // Tenant form settings — supports per-type and legacy flat format
  const tenantSettings = (window.__APP_CONFIG__ as any)?.tenant_settings || {};
  const problemFieldLabel = tenantSettings.problem_field_label || 'Problem Category';
  const rawFormSettings = tenantSettings.ticket_form_settings || {};
  const formSettings = (rawFormSettings[ticketType] && typeof rawFormSettings[ticketType] === 'object')
    ? rawFormSettings[ticketType] : rawFormSettings;
  const subjectRequired = formSettings.subject_required !== false;
  const descriptionRequired = !!formSettings.description_required;
  const locationRequired = !!formSettings.location_required;
  const categoryRequired = !!formSettings.category_required;

  useEffect(() => {
    if (open) {
      loadAll();
      if (isStaff) {
        api.listAllUsers().then(setAllUsers).catch(() => {});
        api.listTeams().then(setTeams).catch(() => {});
        api.listSprints({ status: 'active' }).then(setSprints).catch(() => {});
      }
    }
  }, [open]);

  // Load templates when switching to custom type
  useEffect(() => {
    if (ticketType === 'custom' && open) {
      api.request('GET', '/form-templates/catalog').then((res) => {
        setTemplates(res.all || []);
      }).catch(() => {});
      setSelectedTemplateId(null);
    }
  }, [ticketType, open]);

  // Load template-specific fields when a template is selected
  useEffect(() => {
    if (!selectedTemplateId) return;
    api.listCustomFieldsForForm({ form_template_id: selectedTemplateId, ticket_type: 'custom' })
      .then((res) => {
        setCustomFields(res.fields || []);
        setCustomValues({});
      })
      .catch(() => {});
  }, [selectedTemplateId]);

  // Load custom fields when category or ticket type changes
  useEffect(() => {
    setCustomFields([]);
    setCustomValues({});
    if (!open) return;
    let cancelled = false;
    api.listCustomFieldsForForm({ category_id: problemCategoryId || undefined, ticket_type: ticketType })
      .then((res) => {
        if (cancelled) return;
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
      .catch(() => {});
    return () => { cancelled = true; };
  }, [open, problemCategoryId, ticketType]);

  if (!open) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setErrorFields(new Set());

    const ef = new Set<string>();
    if (subjectRequired && !subject.trim()) ef.add('subject');
    if (descriptionRequired && !description.trim()) ef.add('description');
    if (locationRequired && !locationId) ef.add('location');
    if (categoryRequired && !problemCategoryId) ef.add('category');

    // Custom required-to-create validation
    const missingCustom: string[] = [];
    for (const f of customFields) {
      if (!f.is_required_to_create) continue;
      const v = customValues[f.field_key];
      const empty = v === '' || v === null || v === undefined || (Array.isArray(v) && v.length === 0);
      if (empty) { missingCustom.push(f.name); ef.add(`cf:${f.field_key}`); }
    }

    if (ef.size > 0) {
      const msgs: string[] = [];
      if (ef.has('subject')) msgs.push('Subject');
      if (ef.has('description')) msgs.push('Description');
      if (ef.has('location')) msgs.push('Location');
      if (ef.has('category')) msgs.push(problemFieldLabel);
      if (missingCustom.length > 0) msgs.push(...missingCustom);
      setError(`Required: ${msgs.join(', ')}`);
      setErrorFields(ef);
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
      if (requesterId) payload.requester_id = requesterId;
      if (teamId) payload.team_id = teamId;
      if (selectedTemplateId) payload.form_template_id = selectedTemplateId;
      if (ticketType !== 'support' && storyPoints) payload.story_points = storyPoints;
      if (sprintId) payload.sprint_id = sprintId;
      // Bug built-in fields
      if (ticketType === 'bug') {
        if (stepsToReproduce.trim()) payload.steps_to_reproduce = stepsToReproduce.trim();
        if (expectedBehavior.trim()) payload.expected_behavior = expectedBehavior.trim();
        if (actualBehavior.trim()) payload.actual_behavior = actualBehavior.trim();
      }

      // Attach custom field values
      const cfPayload: Record<string, any> = {};
      for (const f of customFields) {
        const v = customValues[f.field_key];
        const empty = v === '' || v === null || v === undefined || (Array.isArray(v) && v.length === 0);
        if (!empty) cfPayload[f.field_key] = v;
      }
      if (Object.keys(cfPayload).length > 0) payload.custom_fields = cfPayload;

      await createTicket(payload);
      setSubject('');
      setDescription('');
      setLocationId(null);
      setProblemCategoryId(null);
      setRequesterId(null);
      setTeamId(null);
      setCustomFields([]);
      setCustomValues({});
      close(false);
    } catch (err: any) {
      setError(err.message || 'Failed to create ticket');
    } finally {
      setSubmitting(false);
    }
  };

  const handleBackdrop = (e: React.MouseEvent) => {
    if (e.target === e.currentTarget) close(false);
  };

  const updateCustomValue = (key: string, value: any) => {
    setCustomValues((prev) => ({ ...prev, [key]: value }));
  };

  return (
    <div className="modal-backdrop" onClick={handleBackdrop}>
      <div className="modal-container">
        <div className="modal-header">
          <h2 className="modal-title">New Ticket</h2>
          <button className="modal-close" onClick={() => close(false)}>x</button>
        </div>

        <form onSubmit={handleSubmit} className="modal-body">
          {error && <div className="form-error">{error}</div>}

          {isStaff && (() => {
            const hasPermission = useAuthStore.getState().hasPermission;
            const permittedTypes = (['support', 'task', 'bug', 'feature', 'custom'] as const).filter(
              (t) => hasPermission(`tickets.create.${t}`)
            );
            return permittedTypes.length > 1 ? (
              <div className="form-group">
                <label className="form-label">Type</label>
                <div style={{ display: 'flex', gap: 6 }}>
                  {permittedTypes.map((t) => (
                    <button
                      key={t}
                      type="button"
                      className={`btn btn-sm ${ticketType === t ? 'btn-primary' : 'btn-ghost'}`}
                      onClick={() => setTicketType(t)}
                    >
                      {t.charAt(0).toUpperCase() + t.slice(1)}
                    </button>
                  ))}
                </div>
              </div>
            ) : null;
          })()}

          {/* Template picker for Custom type */}
          {ticketType === 'custom' && templates.length > 0 && (
            <div className="form-group">
              <label className="form-label">Select Form</label>
              {(() => {
                // Group templates by catalog_category
                const grouped: Record<string, any[]> = {};
                for (const t of templates) {
                  const cat = t.catalog_category || 'Other';
                  if (!grouped[cat]) grouped[cat] = [];
                  grouped[cat].push(t);
                }
                return (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {Object.entries(grouped).map(([cat, items]) => (
                      <div key={cat}>
                        {Object.keys(grouped).length > 1 && (
                          <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--t-text-dim)', marginBottom: 4 }}>{cat}</div>
                        )}
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                          {items.map((t) => (
                            <button
                              key={t.id}
                              type="button"
                              className={`btn btn-sm ${selectedTemplateId === t.id ? 'btn-primary' : 'btn-ghost'}`}
                              onClick={() => setSelectedTemplateId(t.id)}
                              style={{ fontSize: 12 }}
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

          <div className="form-group">
            <label className="form-label">
              Subject{subjectRequired && <span style={{ color: 'var(--t-warning)', marginLeft: 3 }}>*</span>}
            </label>
            <input
              className="form-input"
              type="text"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              placeholder="Brief description of the issue"
              autoFocus
            />
          </div>

          <div className="form-group">
            <label className="form-label">
              Description{descriptionRequired && <span style={{ color: 'var(--t-warning)', marginLeft: 3 }}>*</span>}
            </label>
            <textarea
              className="form-input form-textarea"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Provide details about the issue..."
              rows={5}
            />
          </div>

          {ticketType === 'bug' && (
            <>
              <div className="form-group">
                <label className="form-label">Steps to Reproduce <span style={{ color: 'var(--t-warning)', marginLeft: 3 }}>*</span></label>
                <textarea
                  className="form-input form-textarea"
                  value={stepsToReproduce}
                  onChange={(e) => setStepsToReproduce(e.target.value)}
                  placeholder="1. Go to...&#10;2. Click on...&#10;3. Observe..."
                  rows={4}
                />
              </div>
              <div className="form-group">
                <label className="form-label">Expected Behavior <span style={{ color: 'var(--t-warning)', marginLeft: 3 }}>*</span></label>
                <textarea
                  className="form-input form-textarea"
                  value={expectedBehavior}
                  onChange={(e) => setExpectedBehavior(e.target.value)}
                  placeholder="What should happen?"
                  rows={3}
                />
              </div>
              <div className="form-group">
                <label className="form-label">Actual Behavior <span style={{ color: 'var(--t-warning)', marginLeft: 3 }}>*</span></label>
                <textarea
                  className="form-input form-textarea"
                  value={actualBehavior}
                  onChange={(e) => setActualBehavior(e.target.value)}
                  placeholder="What actually happens instead?"
                  rows={3}
                />
              </div>
            </>
          )}

          {isStaff && allUsers.length > 0 && (
            <div className="form-group">
              <label className="form-label">Requester (who is this for?)</label>
              <select
                className="form-input form-select"
                value={requesterId ?? ''}
                onChange={(e) => setRequesterId(e.target.value ? parseInt(e.target.value) : null)}
              >
                <option value="">Myself</option>
                {allUsers.map((u) => (
                  <option key={u.id} value={u.id}>{u.name} ({u.email})</option>
                ))}
              </select>
            </div>
          )}

          {isStaff && teams.length > 0 && (
            <div className="form-group">
              <label className="form-label">Team</label>
              <select
                className="form-input form-select"
                value={teamId ?? ''}
                onChange={(e) => setTeamId(e.target.value ? parseInt(e.target.value) : null)}
              >
                <option value="">None</option>
                {teams.map((t) => (
                  <option key={t.id} value={t.id}>{t.name}</option>
                ))}
              </select>
            </div>
          )}

          {isStaff && ticketType !== 'support' && (
            <>
              <div style={{ display: 'flex', gap: 12 }}>
                <div className="form-group" style={{ flex: 1 }}>
                  <label className="form-label">Story Points</label>
                  <input
                    type="number"
                    className="form-input"
                    min={0}
                    max={100}
                    value={storyPoints ?? ''}
                    onChange={(e) => setStoryPoints(e.target.value ? parseInt(e.target.value) : null)}
                    placeholder="Optional"
                  />
                </div>
                {sprints.length > 0 && (
                  <div className="form-group" style={{ flex: 2 }}>
                    <label className="form-label">Sprint</label>
                    <select
                      className="form-input form-select"
                      value={sprintId ?? ''}
                      onChange={(e) => setSprintId(e.target.value ? parseInt(e.target.value) : null)}
                    >
                      <option value="">Backlog (no sprint)</option>
                      {sprints.map((s) => (
                        <option key={s.id} value={s.id}>{s.name} ({s.team_name})</option>
                      ))}
                    </select>
                  </div>
                )}
              </div>
            </>
          )}

          {locations.length > 0 && (
            <div className="form-group">
              <label className="form-label">
                Location{locationRequired && <span style={{ color: 'var(--t-warning)', marginLeft: 3 }}>*</span>}
              </label>
              <CascadingSelect
                items={locations}
                value={locationId}
                onChange={setLocationId}
                placeholder="Select location..."
              />
            </div>
          )}

          {problemCategories.length > 0 && (
            <div className="form-group" style={errorFields.has('category') ? {
              padding: '8px 10px', borderRadius: 6,
              background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.6)',
            } : undefined}>
              <label className="form-label" style={errorFields.has('category') ? { color: 'rgba(239,68,68,1)' } : undefined}>
                {problemFieldLabel}{categoryRequired && <span style={{ color: errorFields.has('category') ? 'rgba(239,68,68,1)' : 'var(--t-warning)', marginLeft: 3 }}>*</span>}
              </label>
              <CascadingSelect
                items={problemCategories}
                value={problemCategoryId}
                onChange={(v) => { setProblemCategoryId(v); setErrorFields((prev) => { const n = new Set(prev); n.delete('category'); return n; }); }}
                placeholder={`Select ${problemFieldLabel.toLowerCase()}...`}
              />
            </div>
          )}

          {/* Custom fields */}
          {customFields.length > 0 && (
            <div style={{ borderTop: '1px solid var(--t-border)', paddingTop: 12, marginTop: 4 }}>
              <div style={{ fontSize: 10, fontWeight: 600, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--t-text-muted)', marginBottom: 10 }}>
                Custom Fields
              </div>
              {customFields.filter((f) => {
                // Hide child fields whose parent condition isn't met
                if (!f.parent_field_id || !f.show_when) return true;
                const parent = customFields.find((p: any) => p.id === f.parent_field_id);
                if (!parent) return true;
                const parentVal = customValues[parent.field_key];
                const triggerVals = f.show_when.values || (f.show_when.value ? [f.show_when.value] : []);
                return triggerVals.some((v: string) => String(parentVal) === String(v));
              }).map((f) => {
                const val = customValues[f.field_key];
                const isReq = f.is_required_to_create;
                const cfErr = errorFields.has(`cf:${f.field_key}`);
                return (
                  <div key={f.id} className="form-group" style={cfErr ? {
                    padding: '8px 10px', borderRadius: 6,
                    background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.6)',
                  } : undefined}>
                    <label className="form-label" style={cfErr ? { color: 'rgba(239,68,68,1)' } : undefined}>
                      {f.name}{isReq && <span style={{ color: cfErr ? 'rgba(239,68,68,1)' : 'var(--t-warning)', marginLeft: 3 }}>*</span>}
                    </label>
                    {f.description && (
                      <div style={{ fontSize: 11, color: 'var(--t-text-muted)', marginBottom: 4, marginTop: -4 }}>{f.description}</div>
                    )}
                    {f.field_type === 'text' && (
                      <input className="form-input" value={val ?? ''} onChange={(e) => updateCustomValue(f.field_key, e.target.value)} placeholder={`Enter ${f.name.toLowerCase()}`} />
                    )}
                    {f.field_type === 'textarea' && (
                      <textarea className="form-input form-textarea" rows={3} value={val ?? ''} onChange={(e) => updateCustomValue(f.field_key, e.target.value)} placeholder={`Enter ${f.name.toLowerCase()}`} />
                    )}
                    {f.field_type === 'number' && (
                      <input className="form-input" type="number" value={val ?? ''} onChange={(e) => updateCustomValue(f.field_key, e.target.value === '' ? '' : Number(e.target.value))} placeholder="0" />
                    )}
                    {f.field_type === 'date' && (
                      <input className="form-input" type="date" value={val ?? ''} onChange={(e) => updateCustomValue(f.field_key, e.target.value)} />
                    )}
                    {f.field_type === 'url' && (
                      <input className="form-input" type="url" value={val ?? ''} onChange={(e) => updateCustomValue(f.field_key, e.target.value)} placeholder="https://" />
                    )}
                    {f.field_type === 'checkbox' && (
                      <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 13 }}>
                        <input type="checkbox" checked={!!val} onChange={(e) => updateCustomValue(f.field_key, e.target.checked)} style={{ accentColor: 'var(--t-accent)' }} />
                        {val ? 'Yes' : 'No'}
                      </label>
                    )}
                    {f.field_type === 'select' && (
                      <select className="form-input form-select" value={val ?? ''} onChange={(e) => updateCustomValue(f.field_key, e.target.value || null)}>
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
                              padding: '3px 10px', borderRadius: 20, fontSize: 11, cursor: 'pointer',
                              border: `1px solid ${checked ? 'var(--t-accent)' : 'var(--t-border)'}`,
                              background: checked ? 'color-mix(in srgb, var(--t-accent) 18%, transparent)' : 'var(--t-bg)',
                              color: checked ? 'var(--t-accent)' : 'var(--t-text-muted)',
                              fontWeight: checked ? 500 : 400,
                            }}>
                              {o.label}
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          <div className="modal-footer">
            <button type="button" className="btn btn-ghost" onClick={() => close(false)} disabled={submitting}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={submitting}>
              {submitting ? 'Creating...' : 'Create Ticket'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
