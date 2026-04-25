/**
 * FormDesigner — WYSIWYG ticket form builder.
 *
 * Admins see the exact form layout end-users will encounter.
 * Per-ticket-type tabs. Built-in fields + custom fields rendered
 * as live preview with inline configuration overlays.
 */
import { useEffect, useState, useCallback } from 'react';
import { api } from '../../api/client';
import { useAuthStore } from '../../store/authStore';
import { useHierarchyStore } from '../../store/hierarchyStore';
import type { CustomFieldDefinition, CustomFieldType, TicketTypeSlug, TicketFormSettings } from '../../types';
import { CascadingSelect } from '../common/CascadingSelect';

// ── constants ──────────────────────────────────────────────

type TicketTypeKey = 'support' | 'task' | 'bug' | 'feature' | 'custom';

const TYPE_LABELS: Record<TicketTypeKey, string> = {
  support: 'Support', task: 'Task', bug: 'Bug', feature: 'Feature', custom: 'Custom',
};
const TYPE_KEYS: TicketTypeKey[] = ['support', 'task', 'bug', 'feature', 'custom'];

const FIELD_TYPE_LABELS: Record<CustomFieldType, string> = {
  text: 'Short Text', textarea: 'Long Text', number: 'Number',
  select: 'Dropdown', multi_select: 'Multi-Select',
  checkbox: 'Checkbox', date: 'Date', url: 'URL',
};

const ALL_TICKET_TYPES: TicketTypeSlug[] = ['support', 'task', 'bug', 'feature', 'custom'];

interface BuiltinFieldConfig {
  key: string;
  label: string;
  settingsKey: keyof TicketFormSettings;
  type: 'input' | 'textarea' | 'cascading';
  placeholder: string;
}

// ── per-type form settings shape ────────────────────────────
interface PerTypeFormSettings {
  subject_required: boolean;
  subject_visible: boolean;
  description_required: boolean;
  description_visible: boolean;
  location_required: boolean;
  location_visible: boolean;
  category_required: boolean;
  category_visible: boolean;
  field_order?: string[]; // ordered field keys: 'subject','description','location','category','cf:123'
}

const DEFAULT_PER_TYPE: PerTypeFormSettings = {
  subject_required: true, subject_visible: true,
  description_required: false, description_visible: true,
  location_required: false, location_visible: true,
  category_required: false, category_visible: true,
};

// Custom type defaults: all built-in fields hidden — form is purely custom fields
const DEFAULT_CUSTOM_TYPE: PerTypeFormSettings = {
  subject_required: false, subject_visible: false,
  description_required: false, description_visible: false,
  location_required: false, location_visible: false,
  category_required: false, category_visible: false,
};

// ── BLANK form for new custom field ────────────────────────
const BLANK_CF = {
  name: '', description: '',
  field_type: 'text' as CustomFieldType,
  options: [] as { label: string; value: string }[],
  applies_to: ALL_TICKET_TYPES as TicketTypeSlug[],
  is_customer_facing: true,
  is_agent_facing: true,
  is_required_to_create: false,
  is_required_to_close: false,
  parent_field_id: null as number | null,
  show_when: null as { value?: string; values?: string[] } | null,
};

// ── styles ──────────────────────────────────────────────────

const S = {
  root: { width: '100%' } as React.CSSProperties,
  tabs: {
    display: 'flex', gap: 0, borderBottom: '2px solid var(--t-border)', marginBottom: 20,
  } as React.CSSProperties,
  tab: (active: boolean) => ({
    padding: '8px 20px', fontSize: 13, fontWeight: active ? 600 : 400,
    color: active ? 'var(--t-accent)' : 'var(--t-text-muted)',
    background: 'none', border: 'none', cursor: 'pointer',
    borderBottom: active ? '2px solid var(--t-accent)' : '2px solid transparent',
    marginBottom: -2, transition: 'all .15s',
  } as React.CSSProperties),
  layout: {
    display: 'grid', gridTemplateColumns: '1fr 320px', gap: 24,
  } as React.CSSProperties,
  previewWrap: {
    background: 'var(--t-bg)', border: '1px solid var(--t-border)', borderRadius: 10,
    padding: 28, minHeight: 400,
  } as React.CSSProperties,
  previewTitle: {
    fontSize: 16, fontWeight: 700, color: 'var(--t-text-bright)', textAlign: 'center' as const,
    marginBottom: 24,
  } as React.CSSProperties,
  fieldWrap: (active: boolean, hovered: boolean) => ({
    position: 'relative' as const, marginBottom: 16,
    padding: '12px 14px', borderRadius: 8,
    border: `1.5px ${active ? 'solid var(--t-accent)' : hovered ? 'dashed var(--t-text-muted)' : 'solid transparent'}`,
    background: active ? 'color-mix(in srgb, var(--t-accent) 5%, transparent)' : 'transparent',
    transition: 'all .15s', cursor: 'grab',
  } as React.CSSProperties),
  fieldLabel: {
    display: 'block', fontSize: 12, fontWeight: 600,
    color: 'var(--t-text-muted)', textTransform: 'uppercase' as const,
    letterSpacing: '0.04em', marginBottom: 6,
  } as React.CSSProperties,
  input: {
    width: '100%', padding: '9px 12px',
    background: 'var(--t-panel)', border: '1px solid var(--t-border)', borderRadius: 6,
    color: 'var(--t-text)', fontSize: 13, outline: 'none', boxSizing: 'border-box' as const,
  } as React.CSSProperties,
  textarea: {
    width: '100%', padding: '9px 12px', resize: 'vertical' as const,
    background: 'var(--t-panel)', border: '1px solid var(--t-border)', borderRadius: 6,
    color: 'var(--t-text)', fontSize: 13, outline: 'none', boxSizing: 'border-box' as const,
    fontFamily: 'inherit', lineHeight: 1.5,
  } as React.CSSProperties,
  select: {
    width: '100%', padding: '9px 12px',
    background: 'var(--t-panel)', border: '1px solid var(--t-border)', borderRadius: 6,
    color: 'var(--t-text-muted)', fontSize: 13, cursor: 'default',
  } as React.CSSProperties,
  sidebar: {
    display: 'flex', flexDirection: 'column' as const, gap: 16,
  } as React.CSSProperties,
  sideSection: {
    background: 'var(--t-panel-alt)', border: '1px solid var(--t-border)', borderRadius: 8,
    padding: 16,
  } as React.CSSProperties,
  sideTitle: {
    fontSize: 11, fontWeight: 600, color: 'var(--t-text-muted)', textTransform: 'uppercase' as const,
    letterSpacing: '0.08em', marginBottom: 10,
  } as React.CSSProperties,
  toggleRow: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: '6px 0', fontSize: 12, color: 'var(--t-text)',
  } as React.CSSProperties,
  badge: (color: string, bg: string) => ({
    fontSize: 10, padding: '1px 6px', borderRadius: 8,
    border: `1px solid ${color}`, color, background: bg,
    fontWeight: 500, whiteSpace: 'nowrap' as const,
  } as React.CSSProperties),
  requiredStar: {
    color: 'var(--t-warning)', marginLeft: 3,
  } as React.CSSProperties,
  hiddenOverlay: {
    position: 'absolute' as const, inset: 0, borderRadius: 8,
    background: 'repeating-linear-gradient(45deg, transparent, transparent 8px, rgba(128,128,128,.06) 8px, rgba(128,128,128,.06) 16px)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    color: 'var(--t-text-dim)', fontSize: 11, fontWeight: 500,
  } as React.CSSProperties,
};

// ── Toggle switch component ──────────────────────────────

function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 12, color: 'var(--t-text)' }}>
      <div
        onClick={(e) => { e.preventDefault(); onChange(!checked); }}
        style={{
          width: 32, height: 18, borderRadius: 9, position: 'relative',
          background: checked ? 'var(--t-accent)' : 'var(--t-border)',
          transition: 'background .15s', cursor: 'pointer', flexShrink: 0,
        }}
      >
        <div style={{
          width: 14, height: 14, borderRadius: '50%', background: '#fff',
          position: 'absolute', top: 2, left: checked ? 16 : 2,
          transition: 'left .15s', boxShadow: '0 1px 2px rgba(0,0,0,.2)',
        }} />
      </div>
      {label}
    </label>
  );
}

// ── Main component ──────────────────────────────────────────

export function FormDesigner() {
  const tenantId = useAuthStore((s) => s.user?.tenant_id);
  const { locations, problemCategories, loadAll } = useHierarchyStore();

  useEffect(() => { loadAll(); }, []);
  const tenantSettings = (window.__APP_CONFIG__ as any)?.tenant_settings || {};
  const problemFieldLabel = tenantSettings.problem_field_label || 'Problem Category';

  const [activeType, setActiveType] = useState<TicketTypeKey>('support');
  const [formConfigs, setFormConfigs] = useState<Record<TicketTypeKey, PerTypeFormSettings>>(() => {
    const stored = tenantSettings.ticket_form_settings || {};
    const configs: any = {};
    for (const t of TYPE_KEYS) {
      const defaults = t === 'custom' ? DEFAULT_CUSTOM_TYPE : DEFAULT_PER_TYPE;
      if (stored[t] && typeof stored[t] === 'object') {
        configs[t] = { ...defaults, ...stored[t] };
      } else {
        // Legacy flat format migration
        configs[t] = {
          ...defaults,
          ...(t !== 'custom' ? {
            subject_required: stored.subject_required !== false,
            description_required: !!stored.description_required,
            location_required: !!stored.location_required,
            category_required: !!stored.category_required,
          } : {}),
        };
      }
    }
    return configs;
  });

  const [customFields, setCustomFields] = useState<CustomFieldDefinition[]>([]);
  const [selectedField, setSelectedField] = useState<string | null>(null); // 'subject' | 'description' | 'location' | 'category' | 'cf:123'
  const [hoveredField, setHoveredField] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [teams, setTeams] = useState<{ id: number; name: string }[]>([]);

  // Category simulation — lets admin preview category-specific fields
  const [simulatedCategoryId, setSimulatedCategoryId] = useState<number | null>(null);
  const [categoryFields, setCategoryFields] = useState<CustomFieldDefinition[]>([]);
  const [loadingCatFields, setLoadingCatFields] = useState(false);

  // Load category-specific fields when simulated category changes
  useEffect(() => {
    setCategoryFields([]);
    if (!simulatedCategoryId) return;
    let cancelled = false;
    setLoadingCatFields(true);
    api.listCustomFieldsForForm({ category_id: simulatedCategoryId, ticket_type: activeType })
      .then((res) => {
        if (cancelled) return;
        // Filter to only category-scoped fields (not global ones, already shown separately)
        const catSpecific = (res.fields || []).filter((f: any) => f.category_id);
        setCategoryFields(catSpecific);
      })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoadingCatFields(false); });
    return () => { cancelled = true; };
  }, [simulatedCategoryId, activeType]);

  // Reset simulation when ticket type changes
  useEffect(() => { setSimulatedCategoryId(null); setCategoryFields([]); }, [activeType]);

  // Custom field editing
  const [editingCF, setEditingCF] = useState<number | 'new' | null>(null);
  const [cfForm, setCfForm] = useState({ ...BLANK_CF });
  const [cfOptionInput, setCfOptionInput] = useState('');
  const [cfError, setCfError] = useState('');
  const [cfSaving, setCfSaving] = useState(false);

  // Form templates (for Custom type)
  const [templates, setTemplates] = useState<any[]>([]);
  const [showTemplateSave, setShowTemplateSave] = useState(false);
  const [templateName, setTemplateName] = useState('');
  const [templateCategory, setTemplateCategory] = useState('');
  const [templateDesc, setTemplateDesc] = useState('');
  const [templateSaving, setTemplateSaving] = useState(false);
  const [templateSubjectFormat, setTemplateSubjectFormat] = useState('');
  const [activeTemplateId, setActiveTemplateId] = useState<number | null>(null);

  const loadTemplates = useCallback(async () => {
    try {
      const res = await api.request('GET', '/form-templates?include_inactive=true');
      setTemplates(res || []);
    } catch {}
  }, []);

  const saveTemplate = async () => {
    if (!templateName.trim()) return;
    setTemplateSaving(true);
    try {
      const allFieldIds = activeCustomFields.map((f) => f.id);
      const payload = {
        name: templateName.trim(),
        description: templateDesc.trim() || null,
        catalog_category: templateCategory.trim() || null,
        ticket_type: 'custom',
        field_ids: allFieldIds,
        subject_format: templateSubjectFormat.trim() || null,
      };
      if (activeTemplateId) {
        await api.request('PUT', `/form-templates/${activeTemplateId}`, payload);
      } else {
        const res = await api.request('POST', '/form-templates', payload);
        setActiveTemplateId(res.id);
      }
      setShowTemplateSave(false);
      await loadTemplates();
    } catch {}
    setTemplateSaving(false);
  };

  const loadTemplate = async (t: any) => {
    setActiveTemplateId(t.id);
    setTemplateName(t.name || '');
    setTemplateCategory(t.catalog_category || '');
    setTemplateDesc(t.description || '');
    setTemplateSubjectFormat(t.subject_format || '');
    setShowTemplateSave(false);
  };

  const clearTemplate = () => {
    setActiveTemplateId(null);
    setTemplateName('');
    setTemplateCategory('');
    setTemplateDesc('');
    setTemplateSubjectFormat('');
    setShowTemplateSave(false);
  };

  const deleteTemplate = async (id: number) => {
    try {
      await api.request('DELETE', `/form-templates/${id}`);
      if (activeTemplateId === id) clearTemplate();
      await loadTemplates();
    } catch {}
  };

  // Load custom fields
  const loadCustomFields = useCallback(async () => {
    try {
      const res = await api.listCustomFields({ include_inactive: true });
      setCustomFields(res.fields || []);
    } catch {}
  }, []);

  useEffect(() => { loadCustomFields(); }, [loadCustomFields]);
  useEffect(() => { api.listTeams().then(setTeams).catch(() => {}); }, []);
  useEffect(() => { loadTemplates(); }, []);

  const cfg = formConfigs[activeType];
  const activeCustomFields = (() => {
    const all = customFields.filter(
      (f) => f.is_active && !f.category_id && (f.applies_to || []).includes(activeType)
    );
    // Custom type is template-driven: show only template's fields when one is loaded,
    // or nothing when starting a new template (clean slate)
    if (activeType === 'custom') {
      if (!activeTemplateId) return [];
      const tmpl = templates.find((t) => t.id === activeTemplateId);
      if (!tmpl) return [];
      const ids = new Set(tmpl.field_ids || []);
      return all.filter((f) => ids.has(f.id));
    }
    return all;
  })();

  // ── Drag-to-reorder ──
  const [dragFrom, setDragFrom] = useState<string | null>(null);

  const builtinKeys = ['subject', 'description', 'location', 'category'];
  const cfKeys = activeCustomFields.map((f) => `cf:${f.id}`);
  const allKeys = [...builtinKeys, ...cfKeys];
  // Use saved field_order if present, filtering out stale entries and appending new ones
  const savedOrder = cfg.field_order || [];
  const orderedKeys = [
    ...savedOrder.filter((k) => allKeys.includes(k)),
    ...allKeys.filter((k) => !savedOrder.includes(k)),
  ];

  const handleFieldDragStart = (key: string) => { setDragFrom(key); };
  const handleFieldDragOver = (e: React.DragEvent, targetKey: string) => {
    e.preventDefault();
    if (!dragFrom || dragFrom === targetKey) return;
  };
  const handleFieldDrop = (targetKey: string) => {
    if (!dragFrom || dragFrom === targetKey) { setDragFrom(null); return; }
    const cur = [...orderedKeys];
    const fi = cur.indexOf(dragFrom);
    const ti = cur.indexOf(targetKey);
    if (fi === -1 || ti === -1) { setDragFrom(null); return; }
    cur.splice(fi, 1);
    cur.splice(ti, 0, dragFrom);
    setDragFrom(null);
    saveConfig(activeType, { ...cfg, field_order: cur });
  };

  // Save form config to tenant settings
  const saveConfig = useCallback(async (type: TicketTypeKey, updated: PerTypeFormSettings) => {
    const newConfigs = { ...formConfigs, [type]: updated };
    setFormConfigs(newConfigs);
    if (!tenantId) return;
    setSaving(true);
    try {
      await api.updateTenantSettings(tenantId, { ticket_form_settings: newConfigs });
      const appCfg = (window as any).__APP_CONFIG__;
      if (appCfg?.tenant_settings) appCfg.tenant_settings.ticket_form_settings = newConfigs;
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
    } catch {}
    setSaving(false);
  }, [formConfigs, tenantId]);

  const toggleBuiltin = (key: string, prop: 'required' | 'visible') => {
    const settingsKey = `${key}_${prop}` as keyof PerTypeFormSettings;
    const updated = { ...cfg, [settingsKey]: !cfg[settingsKey] };
    saveConfig(activeType, updated);
  };

  // Custom field CRUD
  const openNewCF = (categoryId?: number | null) => {
    setCfForm({ ...BLANK_CF, applies_to: [activeType], category_id: categoryId || null } as any);
    setCfOptionInput(''); setCfError(''); setEditingCF('new');
  };
  const openEditCF = (f: CustomFieldDefinition) => {
    setCfForm({
      name: f.name, description: f.description || '',
      field_type: f.field_type, options: f.options || [],
      applies_to: f.applies_to || ALL_TICKET_TYPES,
      is_customer_facing: f.is_customer_facing, is_agent_facing: f.is_agent_facing,
      is_required_to_create: f.is_required_to_create || false,
      is_required_to_close: f.is_required_to_close || false,
      parent_field_id: f.parent_field_id || null,
      show_when: f.show_when || null,
    });
    setCfOptionInput(''); setCfError(''); setEditingCF(f.id);
  };

  const saveCF = async () => {
    if (!cfForm.name.trim()) { setCfError('Name is required'); return; }
    if (!cfForm.is_customer_facing && !cfForm.is_agent_facing) {
      setCfError('At least one visibility option required'); return;
    }
    setCfSaving(true); setCfError('');
    try {
      const payload = { ...cfForm };
      let newFieldId: number | null = null;
      if (editingCF === 'new') {
        const res = await api.createCustomField(payload);
        newFieldId = res?.field?.id || null;
      } else if (typeof editingCF === 'number') {
        await api.updateCustomField(editingCF, payload);
      }
      setEditingCF(null);
      await loadCustomFields();

      // Auto-add new field to active template on Custom tab
      if (newFieldId && activeType === 'custom' && activeTemplateId) {
        const tmpl = templates.find((t) => t.id === activeTemplateId);
        const currentIds = tmpl?.field_ids || [];
        if (!currentIds.includes(newFieldId)) {
          await api.request('PUT', `/form-templates/${activeTemplateId}`, {
            field_ids: [...currentIds, newFieldId],
          });
          await loadTemplates();
        }
      }

      // Refresh category fields if we're simulating
      if (simulatedCategoryId) {
        api.listCustomFieldsForForm({ category_id: simulatedCategoryId, ticket_type: activeType })
          .then((res) => setCategoryFields((res.fields || []).filter((f: any) => f.category_id)))
          .catch(() => {});
      }
    } catch (e: any) { setCfError(e.message); }
    setCfSaving(false);
  };

  const deleteCF = async (f: CustomFieldDefinition) => {
    if (!confirm(`Disable "${f.name}"?`)) return;
    try { await api.deleteCustomField(f.id); await loadCustomFields(); } catch {}
  };

  const addCfOption = () => {
    const val = cfOptionInput.trim(); if (!val) return;
    const slug = val.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
    setCfForm((f) => ({ ...f, options: [...f.options, { label: val, value: slug }] }));
    setCfOptionInput('');
  };

  const toggleCfType = (tt: TicketTypeSlug) =>
    setCfForm((f) => ({
      ...f,
      applies_to: f.applies_to.includes(tt) ? f.applies_to.filter((t) => t !== tt) : [...f.applies_to, tt],
    }));

  // ── Built-in field definitions ──

  const builtinFields: { key: string; label: string; visible: boolean; required: boolean }[] = [
    { key: 'subject', label: 'Subject', visible: cfg.subject_visible, required: cfg.subject_required },
    { key: 'description', label: 'Description', visible: cfg.description_visible, required: cfg.description_required },
    { key: 'location', label: 'Location', visible: cfg.location_visible, required: cfg.location_required },
    { key: 'category', label: problemFieldLabel, visible: cfg.category_visible, required: cfg.category_required },
  ];

  // ── Sidebar for selected field ──

  const renderSidebar = () => {
    if (!selectedField) {
      return (
        <div style={S.sideSection}>
          <div style={S.sideTitle}>Field Properties</div>
          <p style={{ fontSize: 12, color: 'var(--t-text-dim)', margin: 0 }}>
            Click a field in the form preview to configure it.
          </p>
        </div>
      );
    }

    // Built-in field selected
    const builtin = builtinFields.find((b) => b.key === selectedField);
    if (builtin) {
      return (
        <div style={S.sideSection}>
          <div style={S.sideTitle}>{builtin.label}</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <Toggle
              checked={builtin.visible}
              onChange={() => toggleBuiltin(builtin.key, 'visible')}
              label="Visible on form"
            />
            <Toggle
              checked={builtin.required}
              onChange={() => toggleBuiltin(builtin.key, 'required')}
              label="Required to submit"
            />
          </div>
          <p style={{ fontSize: 11, color: 'var(--t-text-dim)', marginTop: 12, marginBottom: 0 }}>
            Built-in field. Toggle visibility and requirement per ticket type.
          </p>
        </div>
      );
    }

    // Custom field selected
    const cfId = selectedField.startsWith('cf:') ? parseInt(selectedField.slice(3)) : null;
    const cf = cfId ? customFields.find((f) => f.id === cfId) : null;
    if (cf) {
      return (
        <div style={S.sideSection}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
            <div style={S.sideTitle}>{cf.name}</div>
            <div style={{ display: 'flex', gap: 4 }}>
              {cf.is_required_to_create && <span style={S.badge('var(--t-warning)', 'rgba(221,221,68,.1)')}>Req. Create</span>}
              {cf.is_required_to_close && <span style={S.badge('var(--t-accent)', 'var(--t-accent-bg)')}>Req. Close</span>}
            </div>
          </div>
          <div style={{ fontSize: 11, color: 'var(--t-text-dim)', marginBottom: 10 }}>
            Type: {FIELD_TYPE_LABELS[cf.field_type]}
            {cf.description && <> &middot; {cf.description}</>}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>
              {cf.is_customer_facing && cf.is_agent_facing ? 'Visible to everyone' :
               cf.is_customer_facing ? 'Customer-facing only' : 'Agent-facing only'}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 6, marginTop: 14 }}>
            <button className="btn btn-ghost btn-sm" style={{ fontSize: 11 }} onClick={() => openEditCF(cf)}>Edit Field</button>
            <button className="btn btn-ghost btn-sm" style={{ fontSize: 11, color: 'var(--t-error)' }} onClick={() => deleteCF(cf)}>Disable</button>
          </div>
        </div>
      );
    }

    return null;
  };

  // ── render field preview ──

  const renderBuiltinPreview = (field: { key: string; label: string; visible: boolean; required: boolean }) => {
    const isActive = selectedField === field.key;
    const isHovered = hoveredField === field.key;

    return (
      <div
        key={field.key}
        style={{ ...S.fieldWrap(isActive, isHovered), opacity: dragFrom === field.key ? 0.4 : 1 }}
        draggable
        onDragStart={() => handleFieldDragStart(field.key)}
        onDragOver={(e) => handleFieldDragOver(e, field.key)}
        onDrop={() => handleFieldDrop(field.key)}
        onClick={() => setSelectedField(field.key)}
        onMouseEnter={() => setHoveredField(field.key)}
        onMouseLeave={() => setHoveredField(null)}
      >
        {!field.visible && <div style={S.hiddenOverlay}>Hidden</div>}
        <label style={{ ...S.fieldLabel, opacity: field.visible ? 1 : 0.35 }}>
          {field.label}
          {field.required && <span style={S.requiredStar}>*</span>}
        </label>

        <div style={{ opacity: field.visible ? 1 : 0.35 }}>
          {field.key === 'subject' && (
            <input style={S.input} disabled placeholder="What do you need help with?" />
          )}
          {field.key === 'description' && (
            <textarea style={S.textarea} rows={4} disabled placeholder="Please provide as much detail as possible..." />
          )}
          {field.key === 'location' && (
            <select style={S.select} disabled>
              <option>Select location...</option>
            </select>
          )}
          {field.key === 'category' && field.visible && (
            <div onClick={(e) => e.stopPropagation()}>
              <CascadingSelect
                items={problemCategories}
                value={simulatedCategoryId}
                onChange={(id) => setSimulatedCategoryId(id)}
                placeholder={`Select ${field.label.toLowerCase()}...`}
              />
              {simulatedCategoryId && (
                <div style={{ marginTop: 6, fontSize: 11, color: 'var(--t-accent)', display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span>Simulating category selection</span>
                  <button
                    type="button"
                    onClick={() => setSimulatedCategoryId(null)}
                    style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--t-text-muted)', fontSize: 10, textDecoration: 'underline' }}
                  >clear</button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    );
  };

  // Get all child fields for a given parent field
  const getChildFields = (parentId: number): CustomFieldDefinition[] => {
    return [...customFields, ...categoryFields].filter((f) => f.parent_field_id === parentId);
  };

  const renderFieldInput = (f: CustomFieldDefinition) => (
    <>
      {f.field_type === 'text' && <input style={S.input} disabled placeholder={`Enter ${f.name.toLowerCase()}`} />}
      {f.field_type === 'textarea' && <textarea style={S.textarea} rows={3} disabled placeholder={`Enter ${f.name.toLowerCase()}`} />}
      {f.field_type === 'number' && <input style={S.input} type="number" disabled placeholder="0" />}
      {f.field_type === 'date' && <input style={S.input} type="date" disabled />}
      {f.field_type === 'url' && <input style={S.input} disabled placeholder="https://" />}
      {f.field_type === 'checkbox' && (
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--t-text)' }}>
          <input type="checkbox" disabled style={{ accentColor: 'var(--t-accent)', width: 15, height: 15 }} /> No
        </label>
      )}
      {f.field_type === 'select' && (
        <select style={S.select} disabled>
          <option>— Select —</option>
          {(f.options || []).map((o) => <option key={o.value}>{o.label}</option>)}
        </select>
      )}
      {f.field_type === 'multi_select' && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {(f.options || []).map((o) => (
            <span key={o.value} style={{
              padding: '4px 12px', borderRadius: 20, fontSize: 12,
              border: '1px solid var(--t-border)', background: 'var(--t-bg)',
              color: 'var(--t-text-muted)',
            }}>
              {o.label}
            </span>
          ))}
        </div>
      )}
    </>
  );

  const renderCustomFieldPreview = (f: CustomFieldDefinition, depth: number = 0): React.ReactNode => {
    // Skip child fields — they render inside their parent
    if (f.parent_field_id && depth === 0) return null;

    const fieldId = `cf:${f.id}`;
    const isActive = selectedField === fieldId;
    const isHovered = hoveredField === fieldId;
    const children = getChildFields(f.id);
    const hasChildren = children.length > 0;

    // Group children by their show_when values
    const childGroups: Map<string, CustomFieldDefinition[]> = new Map();
    for (const child of children) {
      const sw = child.show_when;
      const key = sw?.values?.join(', ') || sw?.value || 'always';
      if (!childGroups.has(key)) childGroups.set(key, []);
      childGroups.get(key)!.push(child);
    }

    return (
      <div
        key={f.id}
        style={{
          ...(depth === 0 ? { ...S.fieldWrap(isActive, isHovered), opacity: dragFrom === fieldId ? 0.4 : 1 } : {}),
          ...(depth > 0 ? {
            padding: '12px 16px', marginBottom: 8, borderRadius: 6,
            border: isActive ? '1px solid var(--t-accent)' : '1px solid var(--t-border)',
            background: 'var(--t-bg)', cursor: 'pointer',
          } : {}),
          ...(hasChildren ? { border: `1px solid ${isActive ? 'var(--t-accent)' : 'var(--t-border)'}`, borderRadius: 8, padding: '14px 16px' } : {}),
        }}
        draggable={depth === 0}
        onDragStart={depth === 0 ? () => handleFieldDragStart(fieldId) : undefined}
        onDragOver={depth === 0 ? (e) => handleFieldDragOver(e, fieldId) : undefined}
        onDrop={depth === 0 ? () => handleFieldDrop(fieldId) : undefined}
        onClick={(e) => { e.stopPropagation(); setSelectedField(fieldId); }}
        onMouseEnter={() => setHoveredField(fieldId)}
        onMouseLeave={() => setHoveredField(null)}
      >
        <label style={S.fieldLabel}>
          {f.name}
          {f.is_required_to_create && <span style={S.requiredStar}>*</span>}
        </label>

        {renderFieldInput(f)}

        {f.description && (
          <div style={{ fontSize: 11, color: 'var(--t-text-dim)', marginTop: 4 }}>{f.description}</div>
        )}

        {/* Nested child sections */}
        {hasChildren && (
          <div style={{ marginTop: 12 }}>
            {Array.from(childGroups.entries()).map(([triggerKey, groupChildren]) => {
              // Resolve trigger value labels
              const triggerLabels = triggerKey === 'always' ? ['Always'] :
                triggerKey.split(', ').map((v) => {
                  const opt = (f.options || []).find((o) => o.value === v);
                  return opt?.label || v;
                });

              return (
                <div key={triggerKey} style={{
                  marginTop: 8, padding: 14, borderRadius: 6,
                  border: '1px solid var(--t-border)',
                  background: 'rgba(255,255,255,.02)',
                  borderLeft: '3px solid var(--t-accent)',
                }}>
                  <div style={{ fontSize: 11, color: 'var(--t-text-muted)', marginBottom: 10 }}>
                    Show this section for
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 12 }}>
                    {triggerLabels.map((label) => (
                      <span key={label} style={{
                        padding: '3px 12px', borderRadius: 14, fontSize: 12,
                        background: 'var(--t-hover)', border: '1px solid var(--t-border)',
                        color: 'var(--t-text)',
                      }}>
                        {label}
                      </span>
                    ))}
                  </div>
                  {groupChildren.map((child) => renderCustomFieldPreview(child, depth + 1))}
                </div>
              );
            })}
          </div>
        )}
      </div>
    );
  };

  return (
    <div style={S.root}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 15, fontWeight: 600, color: 'var(--t-text-bright)' }}>Form Designer</h2>
          <p style={{ margin: '3px 0 0', fontSize: 12, color: 'var(--t-text-muted)' }}>
            Design the ticket creation form your users will see. Drag fields to reorder, click to configure.
          </p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {saving && <span style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>Saving…</span>}
          {saved && <span style={{ fontSize: 11, color: 'var(--t-accent)' }}>Saved</span>}
        </div>
      </div>

      {/* Ticket type tabs */}
      <div style={S.tabs}>
        {TYPE_KEYS.map((t) => (
          <button key={t} style={S.tab(activeType === t)} onClick={() => { setActiveType(t); setSelectedField(null); }}>
            {TYPE_LABELS[t]}
          </button>
        ))}
      </div>

      {/* Layout: preview + sidebar */}
      <div style={S.layout}>
        {/* Left: Form Preview */}
        <div>
          {/* Template management for Custom type */}
          {activeType === 'custom' && (
            <div style={{ marginBottom: 16, padding: 16, background: 'var(--t-panel)', border: '1px solid var(--t-border)', borderRadius: 8 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--t-text-bright)' }}>Form Templates (Service Catalog)</div>
                <div style={{ display: 'flex', gap: 6 }}>
                  {activeTemplateId && (
                    <button className="btn btn-ghost btn-sm" onClick={clearTemplate}>+ New Template</button>
                  )}
                  <button className="btn btn-primary btn-sm" onClick={() => setShowTemplateSave(!showTemplateSave)}>
                    {showTemplateSave ? 'Cancel' : activeTemplateId ? 'Edit Details' : '+ Save as Template'}
                  </button>
                </div>
              </div>

              {/* Active template indicator */}
              {activeTemplateId && !showTemplateSave && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12, padding: '8px 12px', background: 'color-mix(in srgb, var(--t-accent) 10%, transparent)', border: '1px solid var(--t-accent)', borderRadius: 6, fontSize: 12 }}>
                  <span style={{ color: 'var(--t-accent)', fontWeight: 600 }}>Editing:</span>
                  <span style={{ color: 'var(--t-text-bright)' }}>{templateName}</span>
                  {templateCategory && <span style={{ color: 'var(--t-text-dim)' }}>({templateCategory})</span>}
                </div>
              )}

              {showTemplateSave && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: 12, background: 'var(--t-bg)', borderRadius: 6, marginBottom: 12 }}>
                  <input className="form-input" value={templateName} onChange={(e) => setTemplateName(e.target.value)} placeholder="Template name (e.g. Carrot Bucks)" />
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                    <input className="form-input" value={templateCategory} onChange={(e) => setTemplateCategory(e.target.value)} placeholder="Catalog group (e.g. Corporate)" />
                    <input className="form-input" value={templateDesc} onChange={(e) => setTemplateDesc(e.target.value)} placeholder="Description (optional)" />
                  </div>
                  <div>
                    <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--t-text-muted)', display: 'block', marginBottom: 4 }}>
                      Subject Format <span style={{ fontWeight: 400, color: 'var(--t-text-dim)' }}>(optional)</span>
                    </label>
                    <input
                      className="form-input"
                      value={templateSubjectFormat}
                      onChange={(e) => setTemplateSubjectFormat(e.target.value)}
                      placeholder={`e.g. ${templateName || 'Template'} — {{employee_full_name}}`}
                    />
                    <div style={{ fontSize: 10, color: 'var(--t-text-dim)', marginTop: 4 }}>
                      Use <code style={{ background: 'var(--t-hover)', padding: '1px 4px', borderRadius: 3 }}>{'{{field_key}}'}</code> to
                      insert field values. Available: {activeCustomFields.map((f) => f.field_key).join(', ') || 'none yet'}
                    </div>
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--t-text-dim)' }}>
                    {activeTemplateId
                      ? `Update "${templateName}" with the current ${activeCustomFields.length} custom field(s).`
                      : `Save the ${activeCustomFields.length} custom field(s) currently configured as a new template.`}
                  </div>
                  <button className="btn btn-primary btn-sm" style={{ alignSelf: 'flex-start' }} onClick={saveTemplate} disabled={templateSaving || !templateName.trim()}>
                    {templateSaving ? 'Saving...' : activeTemplateId ? 'Update Template' : 'Save Template'}
                  </button>
                </div>
              )}

              {templates.length > 0 ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {templates.map((t) => {
                    const isActive = activeTemplateId === t.id;
                    return (
                      <div key={t.id} onClick={() => !isActive && loadTemplate(t)} style={{
                        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                        padding: '8px 12px', borderRadius: 6, fontSize: 12, cursor: isActive ? 'default' : 'pointer',
                        background: isActive ? 'color-mix(in srgb, var(--t-accent) 8%, var(--t-bg))' : 'var(--t-bg)',
                        border: `1px solid ${isActive ? 'var(--t-accent)' : 'var(--t-border)'}`,
                        transition: 'all .15s',
                      }}>
                        <div>
                          <span style={{ fontWeight: 600, color: isActive ? 'var(--t-accent)' : 'var(--t-text-bright)' }}>{t.name}</span>
                          {t.catalog_category && <span style={{ color: 'var(--t-text-dim)', marginLeft: 8 }}>{t.catalog_category}</span>}
                          {t.description && <span style={{ color: 'var(--t-text-dim)', marginLeft: 8 }}>— {t.description}</span>}
                          <span style={{ color: 'var(--t-text-dim)', marginLeft: 8 }}>({(t.field_ids || []).length} fields)</span>
                        </div>
                        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                          {!t.is_active && <span style={{ fontSize: 10, color: 'var(--t-text-dim)', padding: '2px 6px', background: 'var(--t-hover)', borderRadius: 4 }}>Disabled</span>}
                          <button className="btn btn-ghost btn-xs" style={{ color: 'var(--t-error)', fontSize: 10 }} onClick={(e) => { e.stopPropagation(); deleteTemplate(t.id); }}>Disable</button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div style={{ fontSize: 12, color: 'var(--t-text-dim)', fontStyle: 'italic' }}>
                  No templates saved yet. Design your form below, then save it as a template.
                </div>
              )}
            </div>
          )}

          <div style={S.previewWrap}>
            <div style={S.previewTitle}>
              Submit a New {TYPE_LABELS[activeType]} {activeType === 'support' ? 'Case' : 'Item'}
            </div>

            {/* Fields in drag-reorderable order */}
            {orderedKeys.map((key) => {
              if (builtinKeys.includes(key)) {
                const bf = builtinFields.find((b) => b.key === key);
                if (!bf) return null;
                // Custom type: skip hidden built-in fields entirely (clean slate)
                if (activeType === 'custom' && !bf.visible) return null;
                return renderBuiltinPreview(bf);
              }
              const cfId = key.startsWith('cf:') ? parseInt(key.slice(3)) : null;
              const cf = cfId ? activeCustomFields.find((f) => f.id === cfId) : null;
              return cf ? renderCustomFieldPreview(cf) : null;
            })}

            {/* Category-specific fields (not reorderable — shown when simulating) */}
            {categoryFields.length > 0 && (
              <div style={{ borderTop: '1px solid var(--t-border)', marginTop: 12, paddingTop: 14 }}>
                <div style={{ fontSize: 10, fontWeight: 600, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--t-text-dim)', marginBottom: 8 }}>
                  Category-Specific Fields
                </div>
                {categoryFields.map(renderCustomFieldPreview)}
                {loadingCatFields && (
                  <div style={{ fontSize: 12, color: 'var(--t-text-muted)', padding: '8px 0' }}>Loading category fields…</div>
                )}
              </div>
            )}

            {/* No category selected hint */}
            {!simulatedCategoryId && categoryFields.length === 0 && activeCustomFields.length === 0 && (
              <div style={{
                marginTop: 16, padding: '12px 16px', borderRadius: 6,
                border: '1px dashed var(--t-border)', textAlign: 'center',
              }}>
                <span style={{ fontSize: 12, color: 'var(--t-text-dim)' }}>
                  Select a problem category above to preview its custom fields
                </span>
              </div>
            )}

            {/* Add custom field button */}
            <div style={{ marginTop: 16, textAlign: 'center' }}>
              <button
                type="button"
                onClick={() => openNewCF(simulatedCategoryId)}
                style={{
                  padding: '8px 20px', borderRadius: 6, fontSize: 12, cursor: 'pointer',
                  border: '1.5px dashed var(--t-border)', background: 'transparent',
                  color: 'var(--t-text-muted)', transition: 'all .15s',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.borderColor = 'var(--t-accent)';
                  e.currentTarget.style.color = 'var(--t-accent)';
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.borderColor = 'var(--t-border)';
                  e.currentTarget.style.color = 'var(--t-text-muted)';
                }}
              >
                + Add Custom Field
              </button>
            </div>

            {/* Submit button preview */}
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 24, paddingTop: 16, borderTop: '1px solid var(--t-border)' }}>
              <button type="button" disabled style={{
                padding: '8px 16px', borderRadius: 6, fontSize: 13,
                background: 'transparent', border: '1px solid var(--t-border)',
                color: 'var(--t-text-muted)', cursor: 'default',
              }}>Cancel</button>
              <button type="button" disabled style={{
                padding: '8px 16px', borderRadius: 6, fontSize: 13,
                background: 'var(--t-accent)', border: 'none',
                color: '#000', fontWeight: 600, cursor: 'default',
              }}>
                Submit {activeType === 'support' ? 'Case' : TYPE_LABELS[activeType]}
              </button>
            </div>
          </div>

          {/* Hint */}
          <div style={{ marginTop: 12, padding: '10px 14px', background: 'var(--t-panel-alt)', border: '1px solid var(--t-border)', borderRadius: 6 }}>
            <p style={{ margin: 0, fontSize: 11, color: 'var(--t-text-muted)' }}>
              Select a <strong style={{ color: 'var(--t-text)' }}>Problem Category</strong> in the preview to simulate the end-user experience and see category-specific custom fields.
              Manage category fields from <strong>Categories → select a category → Custom Fields</strong>.
            </p>
          </div>
        </div>

        {/* Right: Sidebar */}
        <div style={S.sidebar}>
          {renderSidebar()}

          {/* Field pool grouped by type */}
          <div style={S.sideSection}>
            <div style={S.sideTitle}>Field Pool</div>
            {/* Built-in fields */}
            <div style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--t-text-dim)', textTransform: 'uppercase', letterSpacing: '0.06em', padding: '4px 0' }}>Built-in</div>
              {builtinFields.map((bf) => (
                <div
                  key={bf.key}
                  style={{
                    ...S.toggleRow,
                    opacity: bf.visible ? 1 : 0.4,
                    cursor: 'pointer', padding: '4px 8px', borderRadius: 4,
                    background: selectedField === bf.key ? 'color-mix(in srgb, var(--t-accent) 10%, transparent)' : 'transparent',
                  }}
                  onClick={() => setSelectedField(bf.key)}
                >
                  <span>{bf.label}</span>
                  <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                    {!bf.visible && <span style={{ fontSize: 10, color: 'var(--t-text-dim)' }}>hidden</span>}
                    {bf.required && <span style={S.badge('var(--t-warning)', 'rgba(221,221,68,.1)')}>req</span>}
                  </div>
                </div>
              ))}
            </div>
            {/* Custom fields grouped by type */}
            {(() => {
              const allCf = [...activeCustomFields, ...categoryFields];
              const types = [...new Set(allCf.map((f) => f.field_type))].sort();
              if (types.length === 0) return (
                <div style={{ fontSize: 11, color: 'var(--t-text-muted)', padding: '4px 0' }}>No custom fields yet</div>
              );
              return types.map((type) => {
                const fieldsOfType = allCf.filter((f) => f.field_type === type);
                return (
                  <div key={type} style={{ marginBottom: 6 }}>
                    <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--t-text-dim)', textTransform: 'uppercase', letterSpacing: '0.06em', padding: '4px 0' }}>
                      {FIELD_TYPE_LABELS[type]} ({fieldsOfType.length})
                    </div>
                    {fieldsOfType.map((f) => (
                      <div
                        key={f.id}
                        style={{
                          ...S.toggleRow,
                          cursor: 'pointer', padding: '4px 8px', borderRadius: 4,
                          background: selectedField === `cf:${f.id}` ? 'color-mix(in srgb, var(--t-accent) 10%, transparent)' : 'transparent',
                        }}
                        onClick={() => setSelectedField(`cf:${f.id}`)}
                      >
                        <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                          {f.name}
                          {f.category_id && <span style={{ fontSize: 9, color: 'var(--t-info)', fontStyle: 'italic' }}>cat</span>}
                        </span>
                        <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                          {f.is_required_to_create && <span style={S.badge('var(--t-warning)', 'rgba(221,221,68,.1)')}>req</span>}
                          {f.is_required_to_close && <span style={S.badge('var(--t-error)', 'rgba(255,68,68,.1)')}>close</span>}
                        </div>
                      </div>
                    ))}
                  </div>
                );
              });
            })()}

            {/* Inactive/disabled fields — re-enable from here */}
            {(() => {
              const inactiveCf = customFields.filter(
                (f) => !f.is_active && !f.category_id && (f.applies_to || []).includes(activeType)
              );
              if (inactiveCf.length === 0) return null;
              return (
                <div style={{ marginTop: 8, borderTop: '1px solid var(--t-border)', paddingTop: 8 }}>
                  <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--t-text-dim)', textTransform: 'uppercase', letterSpacing: '0.06em', padding: '4px 0' }}>
                    Disabled ({inactiveCf.length})
                  </div>
                  {inactiveCf.map((f) => (
                    <div key={f.id} style={{ ...S.toggleRow, padding: '4px 8px', opacity: 0.6 }}>
                      <span style={{ textDecoration: 'line-through', fontSize: 12 }}>{f.name}</span>
                      <button
                        className="btn btn-ghost btn-sm"
                        style={{ fontSize: 10, padding: '1px 6px', color: 'var(--t-success)' }}
                        onClick={async () => {
                          try { await api.updateCustomField(f.id, { is_active: true }); await loadCustomFields(); } catch {}
                        }}
                      >
                        Re-enable
                      </button>
                    </div>
                  ))}
                </div>
              );
            })()}

            {/* Add field button in sidebar — creates for category if one is selected */}
            <button
              className="btn btn-ghost btn-sm"
              style={{ fontSize: 11, marginTop: 8, width: '100%' }}
              onClick={() => openNewCF(simulatedCategoryId)}
            >
              + Add {simulatedCategoryId ? 'Category' : 'Global'} Field
            </button>
          </div>
        </div>
      </div>

      {/* ── Custom Field Modal ──────────────────── */}
      {editingCF !== null && (
        <div
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.6)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={() => setEditingCF(null)}
        >
          <div
            style={{ background: 'var(--t-panel)', border: '1px solid var(--t-border)', borderRadius: 8, width: 560, maxHeight: '90vh', overflowY: 'auto', padding: 24 }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
              <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: 'var(--t-text-bright)' }}>
                {editingCF === 'new' ? 'New Custom Field' : 'Edit Custom Field'}
              </h3>
              <button className="btn btn-ghost btn-sm" onClick={() => setEditingCF(null)}>✕</button>
            </div>

            {cfError && (
              <div style={{ fontSize: 12, color: 'var(--t-error)', padding: '8px 12px', background: 'rgba(255,68,68,.08)', border: '1px solid rgba(255,68,68,.25)', borderRadius: 4, marginBottom: 12 }}>
                {cfError}
              </div>
            )}

            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <span style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>Field Name *</span>
                <input className="form-input" value={cfForm.name} onChange={(e) => setCfForm((f) => ({ ...f, name: e.target.value }))} placeholder="e.g. Order Number" />
              </label>

              <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <span style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>Description</span>
                <input className="form-input" value={cfForm.description} onChange={(e) => setCfForm((f) => ({ ...f, description: e.target.value }))} placeholder="Optional hint text" />
              </label>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  <span style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>Field Type *</span>
                  <select className="form-select" value={cfForm.field_type} onChange={(e) => setCfForm((f) => ({ ...f, field_type: e.target.value as CustomFieldType }))}>
                    {Object.entries(FIELD_TYPE_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                  </select>
                </label>
                <div>
                  <div style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>Ticket Types</div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
                    {ALL_TICKET_TYPES.map((tt) => (
                      <label key={tt} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--t-text)', cursor: 'pointer' }}>
                        <input type="checkbox" checked={cfForm.applies_to.includes(tt)} onChange={() => toggleCfType(tt)} />
                        {TYPE_LABELS[tt]}
                      </label>
                    ))}
                  </div>
                </div>
              </div>

              {(cfForm.field_type === 'select' || cfForm.field_type === 'multi_select') && (
                <div>
                  <div style={{ fontSize: 11, color: 'var(--t-text-muted)', marginBottom: 6 }}>Options * <span style={{ color: 'var(--t-text-dim)' }}>(drag to reorder)</span></div>
                  <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
                    <input className="form-input" style={{ flex: 1 }} value={cfOptionInput} onChange={(e) => setCfOptionInput(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), addCfOption())} placeholder="Add option, press Enter" />
                    <button className="btn btn-ghost btn-sm" onClick={addCfOption}>Add</button>
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                    {cfForm.options.map((o, i) => (
                      <div
                        key={i}
                        draggable
                        onDragStart={(e) => { e.dataTransfer.setData('text/plain', String(i)); }}
                        onDragOver={(e) => e.preventDefault()}
                        onDrop={(e) => {
                          e.preventDefault();
                          const fromIdx = parseInt(e.dataTransfer.getData('text/plain'));
                          if (isNaN(fromIdx) || fromIdx === i) return;
                          setCfForm((f) => {
                            const opts = [...f.options];
                            const [moved] = opts.splice(fromIdx, 1);
                            opts.splice(i, 0, moved);
                            return { ...f, options: opts };
                          });
                        }}
                        style={{
                          fontSize: 12, padding: '6px 10px', background: 'var(--t-hover)',
                          border: '1px solid var(--t-border)', borderRadius: 6,
                          display: 'flex', alignItems: 'center', gap: 8, cursor: 'grab',
                        }}
                      >
                        <span style={{ color: 'var(--t-text-dim)', fontSize: 10, cursor: 'grab' }}>⠿</span>
                        <span style={{ flex: 1 }}>{o.label}</span>
                        <button style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--t-text-muted)', padding: 0, fontSize: 10 }} onClick={() => setCfForm((f) => ({ ...f, options: f.options.filter((_, j) => j !== i) }))}>✕</button>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
                <div>
                  <div style={{ fontSize: 11, color: 'var(--t-text-muted)', marginBottom: 8 }}>Visibility</div>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 12, color: 'var(--t-text)', cursor: 'pointer', marginBottom: 4 }}>
                    <input type="checkbox" checked={cfForm.is_agent_facing} onChange={(e) => setCfForm((f) => ({ ...f, is_agent_facing: e.target.checked }))} />
                    Agent-facing
                  </label>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 12, color: 'var(--t-text)', cursor: 'pointer' }}>
                    <input type="checkbox" checked={cfForm.is_customer_facing} onChange={(e) => setCfForm((f) => ({ ...f, is_customer_facing: e.target.checked }))} />
                    Customer-facing
                  </label>
                </div>
                <div>
                  <div style={{ fontSize: 11, color: 'var(--t-text-muted)', marginBottom: 8 }}>Required</div>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 12, color: 'var(--t-text)', cursor: 'pointer', marginBottom: 4 }}>
                    <input type="checkbox" checked={cfForm.is_required_to_create} onChange={(e) => setCfForm((f) => ({ ...f, is_required_to_create: e.target.checked }))} />
                    Required to Create
                  </label>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 12, color: 'var(--t-text)', cursor: 'pointer' }}>
                    <input type="checkbox" checked={cfForm.is_required_to_close} onChange={(e) => setCfForm((f) => ({ ...f, is_required_to_close: e.target.checked }))} />
                    Required to Close
                  </label>
                </div>
              </div>
            </div>

              {/* Nesting / Conditional Visibility */}
              <div style={{ borderTop: '1px solid var(--t-border)', paddingTop: 14 }}>
                <div style={{ fontSize: 11, color: 'var(--t-text-muted)', marginBottom: 8 }}>Conditional Visibility (Nesting)</div>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4, marginBottom: 10 }}>
                  <span style={{ fontSize: 11, color: 'var(--t-text-dim)' }}>Parent Field</span>
                  <select
                    className="form-select"
                    value={cfForm.parent_field_id ?? ''}
                    onChange={(e) => {
                      const pid = e.target.value ? parseInt(e.target.value) : null;
                      setCfForm((f) => ({ ...f, parent_field_id: pid, show_when: pid ? f.show_when : null }));
                    }}
                  >
                    <option value="">None (always visible)</option>
                    {customFields
                      .filter((f) => f.id !== editingCF && ['select', 'multi_select', 'checkbox'].includes(f.field_type))
                      .map((f) => {
                        // Show hierarchy: indent children, prefix with parent name
                        const depth = f.nesting_depth || 0;
                        const prefix = depth > 0 ? '\u00A0\u00A0'.repeat(depth) + '└ ' : '';
                        return <option key={f.id} value={f.id}>{prefix}{f.name} ({FIELD_TYPE_LABELS[f.field_type]})</option>;
                      })}
                  </select>
                </label>

                {cfForm.parent_field_id && (() => {
                  const parent = customFields.find((f) => f.id === cfForm.parent_field_id);
                  if (!parent) return null;

                  if (parent.field_type === 'checkbox') {
                    return (
                      <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                        <span style={{ fontSize: 11, color: 'var(--t-text-dim)' }}>Show when "{parent.name}" is</span>
                        <select
                          className="form-select"
                          value={cfForm.show_when?.value ?? 'true'}
                          onChange={(e) => setCfForm((f) => ({ ...f, show_when: { value: e.target.value } }))}
                        >
                          <option value="true">Checked</option>
                          <option value="false">Unchecked</option>
                        </select>
                      </label>
                    );
                  }

                  // select / multi_select — show checkboxes for which options trigger visibility
                  const parentOptions = parent.options || [];
                  const selectedValues = cfForm.show_when?.values || (cfForm.show_when?.value ? [cfForm.show_when.value] : []);
                  return (
                    <div>
                      <span style={{ fontSize: 11, color: 'var(--t-text-dim)' }}>Show when "{parent.name}" equals</span>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 6, maxHeight: 150, overflowY: 'auto' }}>
                        {parentOptions.map((o) => (
                          <label key={o.value} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--t-text)', cursor: 'pointer' }}>
                            <input
                              type="checkbox"
                              checked={selectedValues.includes(o.value)}
                              onChange={(e) => {
                                const newVals = e.target.checked
                                  ? [...selectedValues, o.value]
                                  : selectedValues.filter((v) => v !== o.value);
                                setCfForm((f) => ({
                                  ...f,
                                  show_when: newVals.length === 1 ? { value: newVals[0] } : newVals.length > 1 ? { values: newVals } : null,
                                }));
                              }}
                            />
                            {o.label}
                          </label>
                        ))}
                      </div>
                      {parentOptions.length === 0 && (
                        <div style={{ fontSize: 11, color: 'var(--t-text-dim)', fontStyle: 'italic', marginTop: 4 }}>
                          Parent field has no options. Add options to the parent first.
                        </div>
                      )}
                    </div>
                  );
                })()}
              </div>

            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 20, paddingTop: 16, borderTop: '1px solid var(--t-border)' }}>
              <button className="btn btn-ghost" onClick={() => setEditingCF(null)}>Cancel</button>
              <button className="btn btn-primary" onClick={saveCF} disabled={cfSaving}>{cfSaving ? 'Saving…' : 'Save Field'}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
