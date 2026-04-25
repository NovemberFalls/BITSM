/**
 * CustomFieldManager — admin panel for managing global (non-category-scoped) custom fields.
 * Category-specific fields are managed inline from the Categories → TierView panel.
 * Also includes Ticket Form Settings (built-in field requirements).
 */
import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import { useAuthStore } from '../../store/authStore';
import type { CustomFieldDefinition, CustomFieldType, TicketTypeSlug, TicketFormSettings } from '../../types';

const FIELD_TYPE_LABELS: Record<CustomFieldType, string> = {
  text: 'Short Text', textarea: 'Long Text', number: 'Number',
  select: 'Dropdown (single)', multi_select: 'Dropdown (multi)',
  checkbox: 'Checkbox', date: 'Date', url: 'URL',
};

const TICKET_TYPE_LABELS: Record<TicketTypeSlug, string> = {
  support: 'Support', task: 'Task', bug: 'Bug', feature: 'Feature', custom: 'Custom',
};

const ALL_TICKET_TYPES: TicketTypeSlug[] = ['support', 'task', 'bug', 'feature', 'custom'];

const BLANK = {
  name: '', description: '',
  field_type: 'text' as CustomFieldType,
  options: [] as { label: string; value: string }[],
  applies_to: ALL_TICKET_TYPES as TicketTypeSlug[],
  is_customer_facing: false,
  is_agent_facing: true,
  is_required_to_create: false,
  is_required_to_close: false,
};

export function CustomFieldManager() {
  const tenantId = useAuthStore((s) => s.user?.tenant_id);
  const [fields, setFields] = useState<CustomFieldDefinition[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState<number | 'new' | null>(null);
  const [form, setForm] = useState({ ...BLANK });
  const [optionInput, setOptionInput] = useState('');
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [showInactive, setShowInactive] = useState(false);

  // Ticket Form Settings (built-in field requirements)
  const [formSettings, setFormSettings] = useState<TicketFormSettings>({
    subject_required: true,
    description_required: false,
    location_required: false,
    category_required: false,
  });
  const [savingFormSettings, setSavingFormSettings] = useState(false);
  const [formSettingsSaved, setFormSettingsSaved] = useState(false);

  useEffect(() => {
    const ts = (window.__APP_CONFIG__ as any)?.tenant_settings?.ticket_form_settings;
    if (ts && typeof ts === 'object') {
      setFormSettings((prev) => ({ ...prev, ...ts }));
    }
  }, []);

  const saveFormSettings = async (updated: TicketFormSettings) => {
    setFormSettings(updated);
    if (!tenantId) return;
    setSavingFormSettings(true);
    try {
      await api.updateTenantSettings(tenantId, { ticket_form_settings: updated });
      // Update in-memory config so page doesn't need reload
      const cfg = (window as any).__APP_CONFIG__;
      if (cfg?.tenant_settings) cfg.tenant_settings.ticket_form_settings = updated;
      setFormSettingsSaved(true);
      setTimeout(() => setFormSettingsSaved(false), 2000);
    } catch {}
    setSavingFormSettings(false);
  };

  const load = async () => {
    setLoading(true);
    try {
      const res = await api.listCustomFields({ include_inactive: true });
      // Only show global (non-category) fields here
      setFields((res.fields || []).filter((f: any) => !f.category_id));
    } catch (e: any) { setError(e.message); }
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const openNew = () => { setForm({ ...BLANK }); setOptionInput(''); setEditing('new'); setError(''); };
  const openEdit = (f: CustomFieldDefinition) => {
    setForm({
      name: f.name, description: f.description || '',
      field_type: f.field_type, options: f.options || [],
      applies_to: f.applies_to || ALL_TICKET_TYPES,
      is_customer_facing: f.is_customer_facing, is_agent_facing: f.is_agent_facing,
      is_required_to_create: (f as any).is_required_to_create || false,
      is_required_to_close: (f as any).is_required_to_close || false,
    });
    setOptionInput(''); setEditing(f.id); setError('');
  };

  const handleSave = async () => {
    if (!form.name.trim()) { setError('Name is required'); return; }
    if (!form.is_customer_facing && !form.is_agent_facing) {
      setError('At least one visibility option must be selected'); return;
    }
    setSaving(true); setError('');
    try {
      if (editing === 'new') await api.createCustomField(form);
      else if (typeof editing === 'number') await api.updateCustomField(editing, form);
      setEditing(null);
      await load();
    } catch (e: any) { setError(e.message); }
    setSaving(false);
  };

  const handleToggleActive = async (f: CustomFieldDefinition) => {
    try { await api.updateCustomField(f.id, { is_active: !f.is_active }); await load(); }
    catch (e: any) { setError(e.message); }
  };

  const handleDelete = async (f: CustomFieldDefinition) => {
    if (!confirm(`Disable "${f.name}"? Existing values are preserved but the field will be hidden.`)) return;
    try { await api.deleteCustomField(f.id); await load(); }
    catch (e: any) { setError(e.message); }
  };

  const addOption = () => {
    const val = optionInput.trim(); if (!val) return;
    const slug = val.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
    setForm((f) => ({ ...f, options: [...f.options, { label: val, value: slug }] }));
    setOptionInput('');
  };

  const toggleType = (tt: TicketTypeSlug) =>
    setForm((f) => ({
      ...f,
      applies_to: f.applies_to.includes(tt) ? f.applies_to.filter((t) => t !== tt) : [...f.applies_to, tt],
    }));

  const activeFields = fields.filter((f) => f.is_active);
  const inactiveFields = fields.filter((f) => !f.is_active);

  const handleDragOver = (e: React.DragEvent, targetId: number) => {
    e.preventDefault();
    if (dragFrom === null || dragFrom === targetId) return;
    const cur = [...activeFields];
    const fi = cur.findIndex((f) => f.id === dragFrom);
    const ti = cur.findIndex((f) => f.id === targetId);
    if (fi === -1 || ti === -1) return;
    const [moved] = cur.splice(fi, 1);
    cur.splice(ti, 0, moved);
    setFields([...cur, ...inactiveFields]);
  };

  const handleDrop = async () => {
    setDragFrom(null);
    try { await api.reorderCustomFields(activeFields.map((f) => f.id)); } catch {}
  };

  const S = {
    root: { width: '100%' } as React.CSSProperties,
    header: { display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 20 } as React.CSSProperties,
    title: { margin: 0, fontSize: 14, fontWeight: 600, color: 'var(--t-text-bright)' } as React.CSSProperties,
    sub: { margin: '4px 0 0', fontSize: 12, color: 'var(--t-text-muted)' } as React.CSSProperties,
    row: { display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px', background: 'var(--t-panel-alt)', border: '1px solid var(--t-border)', borderRadius: 6, marginBottom: 6, cursor: 'grab' } as React.CSSProperties,
    fieldName: { fontSize: 13, fontWeight: 500, color: 'var(--t-text-bright)', flex: 1 } as React.CSSProperties,
    meta: { fontSize: 11, color: 'var(--t-text-muted)' } as React.CSSProperties,
    badge: (color: string, bg: string, border: string) => ({ fontSize: 10, padding: '2px 7px', borderRadius: 10, border: `1px solid ${border}`, color, background: bg } as React.CSSProperties),
    err: { fontSize: 12, color: 'var(--t-error)', padding: '8px 12px', background: 'rgba(255,68,68,.08)', border: '1px solid rgba(255,68,68,.25)', borderRadius: 4, marginBottom: 12 } as React.CSSProperties,
    label: { display: 'flex', flexDirection: 'column', gap: 4 } as React.CSSProperties,
    labelText: { fontSize: 11, color: 'var(--t-text-muted)' } as React.CSSProperties,
    checkRow: { display: 'flex', alignItems: 'center', gap: 7, fontSize: 12, color: 'var(--t-text)', cursor: 'pointer', marginBottom: 4 } as React.CSSProperties,
    dim: { fontSize: 10, color: 'var(--t-text-dim)' } as React.CSSProperties,
  };

  const BUILTIN_FIELDS: { key: keyof TicketFormSettings; label: string; hint: string }[] = [
    { key: 'subject_required', label: 'Subject', hint: 'Ticket subject/title' },
    { key: 'description_required', label: 'Description', hint: 'Ticket description body' },
    { key: 'location_required', label: 'Location', hint: 'Location hierarchy' },
    { key: 'category_required', label: 'Problem Category', hint: 'Problem category hierarchy' },
  ];

  return (
    <div style={S.root}>
      {/* ── Ticket Form Settings ───────────────────── */}
      <div style={{ marginBottom: 28, padding: '16px 20px', background: 'var(--t-panel-alt)', border: '1px solid var(--t-border)', borderRadius: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <div>
            <h3 style={{ margin: 0, fontSize: 13, fontWeight: 600, color: 'var(--t-text-bright)' }}>Ticket Form Settings</h3>
            <p style={{ margin: '3px 0 0', fontSize: 11, color: 'var(--t-text-muted)' }}>
              Control which built-in fields are required when creating a ticket (portal + agent).
            </p>
          </div>
          {savingFormSettings && <span style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>Saving…</span>}
          {formSettingsSaved && <span style={{ fontSize: 11, color: 'var(--t-accent)' }}>Saved</span>}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 8 }}>
          {BUILTIN_FIELDS.map((bf) => (
            <label key={bf.key} style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '8px 12px', borderRadius: 6,
              background: formSettings[bf.key] ? 'color-mix(in srgb, var(--t-accent) 10%, transparent)' : 'var(--t-bg)',
              border: `1px solid ${formSettings[bf.key] ? 'var(--t-accent-border)' : 'var(--t-border)'}`,
              cursor: 'pointer', transition: 'all .15s',
            }}>
              <input
                type="checkbox"
                checked={!!formSettings[bf.key]}
                onChange={(e) => saveFormSettings({ ...formSettings, [bf.key]: e.target.checked })}
                style={{ accentColor: 'var(--t-accent)', width: 14, height: 14 }}
              />
              <div>
                <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--t-text)' }}>{bf.label}</div>
                <div style={{ fontSize: 10, color: 'var(--t-text-muted)' }}>{bf.hint}</div>
              </div>
            </label>
          ))}
        </div>
      </div>

      {/* ── Global Custom Fields ───────────────────── */}
      <div style={S.header}>
        <div>
          <h2 style={S.title}>Custom Fields</h2>
          <p style={S.sub}>
            Global fields appear on all matching ticket types. Category-specific fields are managed from{' '}
            <strong style={{ color: 'var(--t-text)' }}>Categories → select a category → Custom Fields</strong>.
          </p>
        </div>
        <button className="btn btn-primary btn-sm" onClick={openNew}>+ Add Field</button>
      </div>

      {error && !editing && <div style={S.err}>{error}</div>}

      {loading ? (
        <div style={{ fontSize: 12, color: 'var(--t-text-muted)' }}>Loading…</div>
      ) : (
        <>
          {activeFields.length === 0 && (
            <div style={{ padding: '32px', textAlign: 'center', color: 'var(--t-text-dim)', border: '1px dashed var(--t-border)', borderRadius: 6, fontSize: 13 }}>
              No global custom fields yet. Add one above, or manage category-specific fields from the Categories tab.
            </div>
          )}

          {activeFields.map((f) => (
            <div
              key={f.id}
              style={{ ...S.row, opacity: dragFrom === f.id ? 0.5 : 1 }}
              draggable
              onDragStart={() => setDragFrom(f.id)}
              onDragOver={(e) => handleDragOver(e, f.id)}
              onDrop={handleDrop}
            >
              <span style={{ color: 'var(--t-text-dim)', fontSize: 14, cursor: 'grab' }}>⠿</span>
              <div style={{ flex: 1 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', marginBottom: 2 }}>
                  <span style={S.fieldName}>{f.name}</span>
                  {(f as any).is_required_to_create && <span style={S.badge('var(--t-warning)', 'rgba(221,221,68,.1)', 'rgba(221,221,68,.3)')}>Req. Create</span>}
                  {(f as any).is_required_to_close && <span style={S.badge('var(--t-accent)', 'var(--t-accent-bg)', 'var(--t-accent-border)')}>Req. Close</span>}
                  {f.is_customer_facing && <span style={S.badge('var(--t-info)', 'rgba(68,221,221,.1)', 'rgba(68,221,221,.3)')}>Customer</span>}
                  {f.is_agent_facing && <span style={S.badge('var(--t-text-muted)', 'var(--t-hover)', 'var(--t-border)')}>Agent</span>}
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  <span style={S.meta}>{FIELD_TYPE_LABELS[f.field_type]}</span>
                  <span style={S.meta}>·</span>
                  <span style={S.meta}>{(f.applies_to || []).map((t) => TICKET_TYPE_LABELS[t as TicketTypeSlug]).join(', ')}</span>
                  {f.description && <><span style={S.meta}>·</span><span style={S.meta}>{f.description}</span></>}
                </div>
              </div>
              <button className="btn btn-ghost btn-sm" style={{ fontSize: 11 }} onClick={() => openEdit(f)}>Edit</button>
              <button className="btn btn-ghost btn-sm" style={{ fontSize: 11, color: 'var(--t-error)' }} onClick={() => handleDelete(f)}>Disable</button>
            </div>
          ))}

          {inactiveFields.length > 0 && (
            <div style={{ marginTop: 20 }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', marginBottom: 10 }}>
                <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} style={{ accentColor: 'var(--t-accent)' }} />
                <span style={{ fontSize: 12, color: 'var(--t-text-muted)' }}>Include inactive ({inactiveFields.length} disabled)</span>
              </label>
              {showInactive && (
                <div style={{ opacity: 0.7, borderTop: '1px solid var(--t-border)', paddingTop: 8 }}>
                  {inactiveFields.map((f) => (
                    <div key={f.id} style={{ ...S.row, cursor: 'default' }}>
                      <span style={{ ...S.fieldName, textDecoration: 'line-through', color: 'var(--t-text-muted)' }}>{f.name}</span>
                      <span className="badge" style={{ fontSize: 10, marginLeft: 8, padding: '1px 6px', background: 'var(--t-error)', color: '#fff', borderRadius: 4 }}>Disabled</span>
                      <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                        <button className="btn btn-ghost btn-sm" style={{ fontSize: 11 }} onClick={() => openEdit(f)}>Edit</button>
                        <button className="btn btn-ghost btn-sm" style={{ fontSize: 11, color: 'var(--t-success)' }} onClick={() => handleToggleActive(f)}>Re-enable</button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}

      {/* Modal */}
      {editing !== null && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.6)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={() => setEditing(null)}>
          <div style={{ background: 'var(--t-panel)', border: '1px solid var(--t-border)', borderRadius: 8, width: 560, maxHeight: '90vh', overflowY: 'auto', padding: 24 }}
            onClick={(e) => e.stopPropagation()}>

            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
              <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: 'var(--t-text-bright)' }}>
                {editing === 'new' ? 'New Global Custom Field' : 'Edit Custom Field'}
              </h3>
              <button className="btn btn-ghost btn-sm" onClick={() => setEditing(null)}>✕</button>
            </div>

            {error && <div style={S.err}>{error}</div>}

            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <label style={S.label}>
                <span style={S.labelText}>Field Name *</span>
                <input className="form-input" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} placeholder="e.g. Carrot Bucks Amount" />
              </label>

              <label style={S.label}>
                <span style={S.labelText}>Description</span>
                <input className="form-input" value={form.description} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} placeholder="Optional hint for agents/customers" />
              </label>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <label style={S.label}>
                  <span style={S.labelText}>Field Type *</span>
                  <select className="form-select" value={form.field_type} onChange={(e) => setForm((f) => ({ ...f, field_type: e.target.value as CustomFieldType }))}>
                    {Object.entries(FIELD_TYPE_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                  </select>
                </label>

                <div>
                  <div style={S.labelText}>Applies to Types</div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
                    {ALL_TICKET_TYPES.map((tt) => (
                      <label key={tt} style={S.checkRow}>
                        <input type="checkbox" checked={form.applies_to.includes(tt)} onChange={() => toggleType(tt)} />
                        {TICKET_TYPE_LABELS[tt]}
                      </label>
                    ))}
                  </div>
                </div>
              </div>

              {(form.field_type === 'select' || form.field_type === 'multi_select') && (
                <div>
                  <div style={{ ...S.labelText, marginBottom: 6 }}>Options *</div>
                  <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
                    <input className="form-input" style={{ flex: 1 }} value={optionInput} onChange={(e) => setOptionInput(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), addOption())} placeholder="Add option, press Enter" />
                    <button className="btn btn-ghost btn-sm" onClick={addOption}>Add</button>
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {form.options.map((o, i) => (
                      <span key={i} style={{ fontSize: 12, padding: '2px 10px', background: 'var(--t-hover)', border: '1px solid var(--t-border)', borderRadius: 10, display: 'flex', gap: 5, alignItems: 'center' }}>
                        {o.label}
                        <button style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--t-text-muted)', padding: 0, fontSize: 10 }} onClick={() => setForm((f) => ({ ...f, options: f.options.filter((_, j) => j !== i) }))}>✕</button>
                      </span>
                    ))}
                    {form.options.length === 0 && <span style={S.dim}>No options yet</span>}
                  </div>
                </div>
              )}

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
                <div>
                  <div style={{ ...S.labelText, marginBottom: 8 }}>Visibility</div>
                  <label style={S.checkRow}>
                    <input type="checkbox" checked={form.is_agent_facing} onChange={(e) => setForm((f) => ({ ...f, is_agent_facing: e.target.checked }))} />
                    <span>Agent-facing <span style={S.dim}>(staff see this)</span></span>
                  </label>
                  <label style={S.checkRow}>
                    <input type="checkbox" checked={form.is_customer_facing} onChange={(e) => setForm((f) => ({ ...f, is_customer_facing: e.target.checked }))} />
                    <span>Customer-facing <span style={S.dim}>(portal users)</span></span>
                  </label>
                </div>
                <div>
                  <div style={{ ...S.labelText, marginBottom: 8 }}>Required</div>
                  <label style={S.checkRow}>
                    <input type="checkbox" checked={(form as any).is_required_to_create} onChange={(e) => setForm((f) => ({ ...f, is_required_to_create: e.target.checked } as any))} />
                    <span>Required to Create <span style={S.dim}>(blocks submission)</span></span>
                  </label>
                  <label style={S.checkRow}>
                    <input type="checkbox" checked={(form as any).is_required_to_close} onChange={(e) => setForm((f) => ({ ...f, is_required_to_close: e.target.checked } as any))} />
                    <span>Required to Close <span style={S.dim}>(Atlas collects before close)</span></span>
                  </label>
                </div>
              </div>
            </div>

            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 20, paddingTop: 16, borderTop: '1px solid var(--t-border)' }}>
              <button className="btn btn-ghost" onClick={() => setEditing(null)}>Cancel</button>
              <button className="btn btn-primary" onClick={handleSave} disabled={saving}>{saving ? 'Saving…' : 'Save Field'}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
