import { useState, useEffect } from 'react';
import { api } from '../../api/client';
import type { CustomFieldDefinition, CustomFieldType } from '../../types';

interface TierItem {
  id: number;
  parent_id: number | null;
  name: string;
  sort_order: number;
  [key: string]: any;
}

interface TierViewProps {
  items: TierItem[];
  onCreate: (data: { name: string; parent_id?: number | null }) => Promise<any>;
  onUpdate: (id: number, data: Partial<TierItem>) => Promise<any>;
  onDelete: (id: number) => Promise<any>;
  tierLabels?: string[];
  showPriority?: boolean;
  showTeam?: boolean;
  teams?: { id: number; name: string }[];
  showContactInfo?: boolean;
}

const FIELD_TYPE_LABELS: Record<CustomFieldType, string> = {
  text: 'Short Text', textarea: 'Long Text', number: 'Number',
  select: 'Dropdown', multi_select: 'Multi-select', checkbox: 'Checkbox',
  date: 'Date', url: 'URL',
};

const BLANK: Partial<CustomFieldDefinition> & { options: {label:string;value:string}[] } = {
  name: '', description: '', field_type: 'text', options: [],
  is_customer_facing: false, is_agent_facing: true,
  is_required_to_create: false, is_required_to_close: false,
};

function CategoryCustomFields({ categoryId }: { categoryId: number }) {
  const [fields, setFields] = useState<CustomFieldDefinition[]>([]);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState<number | 'new' | null>(null);
  const [form, setForm] = useState({ ...BLANK });
  const [optionInput, setOptionInput] = useState('');
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState('');

  const load = async () => {
    setLoading(true);
    try {
      const res = await api.listCustomFields({ include_inactive: false });
      setFields((res.fields || []).filter((f: CustomFieldDefinition) => f.category_id === categoryId));
    } catch {}
    setLoading(false);
  };

  useEffect(() => { load(); }, [categoryId]);

  const openNew = () => {
    setForm({ ...BLANK });
    setOptionInput('');
    setErr('');
    setEditing('new');
  };

  const openEdit = (f: CustomFieldDefinition) => {
    setForm({
      name: f.name, description: f.description || '',
      field_type: f.field_type, options: f.options || [],
      is_customer_facing: f.is_customer_facing, is_agent_facing: f.is_agent_facing,
      is_required_to_create: (f as any).is_required_to_create || false,
      is_required_to_close: (f as any).is_required_to_close || false,
    });
    setOptionInput('');
    setErr('');
    setEditing(f.id);
  };

  const save = async () => {
    if (!form.name?.trim()) { setErr('Name is required'); return; }
    if (!form.is_customer_facing && !form.is_agent_facing) {
      setErr('At least one visibility must be enabled'); return;
    }
    setSaving(true); setErr('');
    try {
      const payload = { ...form, category_id: categoryId };
      if (editing === 'new') {
        await api.createCustomField(payload);
      } else if (typeof editing === 'number') {
        await api.updateCustomField(editing, payload);
      }
      setEditing(null);
      await load();
    } catch (e: any) { setErr(e.message); }
    setSaving(false);
  };

  const remove = async (f: CustomFieldDefinition) => {
    if (!confirm(`Remove "${f.name}" from this category?`)) return;
    await api.deleteCustomField(f.id);
    await load();
  };

  const addOption = () => {
    const v = optionInput.trim(); if (!v) return;
    const slug = v.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
    setForm((p) => ({ ...p, options: [...(p.options||[]), { label: v, value: slug }] }));
    setOptionInput('');
  };

  if (loading) return <span style={{ fontSize: 12, color: 'var(--t-text-muted)' }}>Loading…</span>;

  return (
    <div style={{ marginTop: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
        <span style={{ fontSize: 12, color: 'var(--t-text-muted)', fontWeight: 600 }}>Custom Fields</span>
        <span style={{ fontSize: 11, color: 'var(--t-text-dim)', background: 'var(--t-border)', borderRadius: 10, padding: '1px 7px' }}>{fields.length}</span>
        <button
          className="btn btn-ghost btn-sm"
          style={{ fontSize: 11, padding: '2px 8px', marginLeft: 'auto' }}
          onClick={openNew}
        >+ Add Field</button>
      </div>

      {fields.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginBottom: 8 }}>
          {fields.map((f) => (
            <div key={f.id} style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '5px 10px', background: 'var(--t-panel-alt)',
              border: '1px solid var(--t-border)', borderRadius: 4,
            }}>
              <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--t-text-bright)', flex: 1 }}>{f.name}</span>
              <span style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>{FIELD_TYPE_LABELS[f.field_type]}</span>
              {(f as any).is_required_to_create && <span style={{ fontSize: 10, color: 'var(--t-warning)', background: 'rgba(221,221,68,.1)', border: '1px solid rgba(221,221,68,.3)', borderRadius: 3, padding: '1px 5px' }}>Req. Create</span>}
              {(f as any).is_required_to_close && <span style={{ fontSize: 10, color: 'var(--t-accent)', background: 'var(--t-accent-bg)', border: '1px solid var(--t-accent-border)', borderRadius: 3, padding: '1px 5px' }}>Req. Close</span>}
              {f.is_customer_facing && <span style={{ fontSize: 10, color: 'var(--t-info)', background: 'rgba(68,221,221,.1)', border: '1px solid rgba(68,221,221,.3)', borderRadius: 3, padding: '1px 5px' }}>Customer</span>}
              {f.is_agent_facing && <span style={{ fontSize: 10, color: 'var(--t-text-muted)', background: 'var(--t-hover)', border: '1px solid var(--t-border)', borderRadius: 3, padding: '1px 5px' }}>Agent</span>}
              <button className="btn btn-ghost btn-sm" style={{ fontSize: 11, padding: '1px 7px' }} onClick={() => openEdit(f)}>Edit</button>
              <button className="btn btn-ghost btn-sm" style={{ fontSize: 11, padding: '1px 7px', color: 'var(--t-error)' }} onClick={() => remove(f)}>✕</button>
            </div>
          ))}
        </div>
      )}

      {fields.length === 0 && editing === null && (
        <div style={{ fontSize: 11, color: 'var(--t-text-dim)', fontStyle: 'italic' }}>No custom fields for this category yet.</div>
      )}

      {/* Inline editor */}
      {editing !== null && (
        <div style={{ background: 'var(--t-panel-alt)', border: '1px solid var(--t-accent-border)', borderRadius: 6, padding: 16, marginTop: 8 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--t-text-bright)', marginBottom: 12 }}>
            {editing === 'new' ? 'New Custom Field' : 'Edit Custom Field'}
          </div>
          {err && <div style={{ fontSize: 12, color: 'var(--t-error)', marginBottom: 8 }}>{err}</div>}

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 10 }}>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>Field Name *</span>
              <input className="form-input" value={form.name||''} onChange={(e) => setForm(p=>({...p, name: e.target.value}))} placeholder="e.g. Account Number" />
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>Type *</span>
              <select className="form-select" value={form.field_type||'text'} onChange={(e) => setForm(p=>({...p, field_type: e.target.value as CustomFieldType}))}>
                {Object.entries(FIELD_TYPE_LABELS).map(([v,l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </label>
          </div>

          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, marginBottom: 10 }}>
            <span style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>Description</span>
            <input className="form-input" value={form.description||''} onChange={(e) => setForm(p=>({...p, description: e.target.value}))} placeholder="Optional hint" />
          </label>

          {(form.field_type === 'select' || form.field_type === 'multi_select') && (
            <div style={{ marginBottom: 10 }}>
              <span style={{ fontSize: 11, color: 'var(--t-text-muted)', display: 'block', marginBottom: 4 }}>Options *</span>
              <div style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
                <input className="form-input" style={{ flex: 1 }} value={optionInput} onChange={(e) => setOptionInput(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), addOption())} placeholder="Add option, press Enter" />
                <button className="btn btn-ghost btn-sm" onClick={addOption}>Add</button>
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {(form.options||[]).map((o,i) => (
                  <span key={i} style={{ fontSize: 11, padding: '2px 8px', background: 'var(--t-hover)', border: '1px solid var(--t-border)', borderRadius: 10, display: 'flex', alignItems: 'center', gap: 4 }}>
                    {o.label}
                    <button style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--t-text-muted)', padding: 0, fontSize: 10 }} onClick={() => setForm(p => ({ ...p, options: (p.options||[]).filter((_,j)=>j!==i) }))}>✕</button>
                  </span>
                ))}
              </div>
            </div>
          )}

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 10 }}>
            <div>
              <div style={{ fontSize: 11, color: 'var(--t-text-muted)', marginBottom: 6 }}>Visibility</div>
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, marginBottom: 4, cursor: 'pointer' }}>
                <input type="checkbox" checked={!!form.is_agent_facing} onChange={(e) => setForm(p=>({...p, is_agent_facing: e.target.checked}))} />
                Agent-facing <span style={{ fontSize: 10, color: 'var(--t-text-dim)' }}>(backend staff)</span>
              </label>
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, cursor: 'pointer' }}>
                <input type="checkbox" checked={!!form.is_customer_facing} onChange={(e) => setForm(p=>({...p, is_customer_facing: e.target.checked}))} />
                Customer-facing <span style={{ fontSize: 10, color: 'var(--t-text-dim)' }}>(portal users)</span>
              </label>
            </div>
            <div>
              <div style={{ fontSize: 11, color: 'var(--t-text-muted)', marginBottom: 6 }}>Required</div>
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, marginBottom: 4, cursor: 'pointer' }}>
                <input type="checkbox" checked={!!(form as any).is_required_to_create} onChange={(e) => setForm(p=>({...p, is_required_to_create: e.target.checked} as any))} />
                Required to Create <span style={{ fontSize: 10, color: 'var(--t-text-dim)' }}>(blocks submission)</span>
              </label>
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, cursor: 'pointer' }}>
                <input type="checkbox" checked={!!(form as any).is_required_to_close} onChange={(e) => setForm(p=>({...p, is_required_to_close: e.target.checked} as any))} />
                Required to Close <span style={{ fontSize: 10, color: 'var(--t-text-dim)' }}>(Atlas collects before close)</span>
              </label>
            </div>
          </div>

          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button className="btn btn-ghost btn-sm" onClick={() => setEditing(null)}>Cancel</button>
            <button className="btn btn-primary btn-sm" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save Field'}</button>
          </div>
        </div>
      )}
    </div>
  );
}

export function TierView({ items, onCreate, onUpdate, onDelete, tierLabels: customLabels, showPriority = false, showTeam = false, teams = [], showContactInfo = false }: TierViewProps) {
  const [selectedPath, setSelectedPath] = useState<(number | null)[]>([null]);
  const [addingAt, setAddingAt] = useState<number | null>(null);
  const [newName, setNewName] = useState('');
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editName, setEditName] = useState('');
  const [editPhone, setEditPhone] = useState('');
  const [editEmail, setEditEmail] = useState('');
  const [autoSelected, setAutoSelected] = useState(false);
  const [showCustomFields, setShowCustomFields] = useState(false);

  const childrenOf = (parentId: number | null): TierItem[] =>
    items.filter((i) => i.parent_id === parentId).sort((a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name));

  const tiers: { parentId: number | null; items: TierItem[]; selectedId: number | null }[] = [];
  const roots = childrenOf(null);
  tiers.push({ parentId: null, items: roots, selectedId: selectedPath[0] ?? null });

  for (let i = 0; i < selectedPath.length; i++) {
    const selectedId = selectedPath[i];
    if (selectedId == null) break;
    const children = childrenOf(selectedId);
    // Always show the next tier column when an item is selected —
    // even with zero children, the user needs the "+ Add" button to create subcategories.
    tiers.push({ parentId: selectedId, items: children, selectedId: selectedPath[i + 1] ?? null });
  }

  useEffect(() => {
    if (!autoSelected && selectedPath.length === 1 && selectedPath[0] === null && items.length > 0) {
      const roots = items.filter(i => i.parent_id === null);
      const firstWithChildren = roots.find(root => items.some(child => child.parent_id === root.id));
      if (firstWithChildren) {
        setSelectedPath([firstWithChildren.id]);
        setAutoSelected(true);
      }
    }
  }, [items, autoSelected, selectedPath]);

  // Reset custom fields panel when selection changes
  useEffect(() => { setShowCustomFields(false); }, [selectedPath.join(',')]);

  const deriveTierLabel = (tierIdx: number): string => {
    if (customLabels && customLabels[tierIdx]) return customLabels[tierIdx];
    if (tierIdx === 0) {
      const root = roots.find(r => r.level_label);
      if (root?.level_label) return root.level_label;
    } else {
      const parentId = selectedPath[tierIdx - 1];
      if (parentId != null) {
        const children = childrenOf(parentId);
        const child = children.find(c => c.level_label);
        if (child?.level_label) return child.level_label;
      }
    }
    const defaults = ['System', 'Tier 1', 'Tier 2', 'Tier 3', 'Tier 4'];
    return defaults[tierIdx] || `Tier ${tierIdx}`;
  };

  const handleSelect = (tierIndex: number, itemId: number) => {
    const newPath = [...selectedPath.slice(0, tierIndex)];
    if (selectedPath[tierIndex] === itemId) {
      newPath[tierIndex] = null;
    } else {
      newPath[tierIndex] = itemId;
    }
    setSelectedPath(newPath);
    setAddingAt(null);
    setEditingId(null);
  };

  const handleAdd = async (tierIndex: number) => {
    if (!newName.trim()) return;
    const parentId = tierIndex === 0 ? null : selectedPath[tierIndex - 1] ?? null;
    await onCreate({ name: newName.trim(), parent_id: parentId });
    setNewName('');
    setAddingAt(null);
  };

  const handleSaveEdit = async () => {
    if (!editingId || !editName.trim()) return;
    const updateData: Partial<TierItem> = { name: editName.trim() };
    if (showContactInfo) {
      updateData.phone = editPhone.trim() || null;
      updateData.email = editEmail.trim() || null;
    }
    await onUpdate(editingId, updateData);
    setEditingId(null);
    setEditName('');
  };

  const handleDelete = async (id: number) => {
    const children = childrenOf(id);
    if (children.length > 0) {
      if (!confirm('This will also remove all sub-items. Continue?')) return;
      for (const child of children) {
        const grandchildren = childrenOf(child.id);
        for (const gc of grandchildren) await onDelete(gc.id);
        await onDelete(child.id);
      }
    }
    await onDelete(id);
    setSelectedPath((prev) => {
      const idx = prev.indexOf(id);
      return idx >= 0 ? prev.slice(0, idx) : prev;
    });
  };

  const breadcrumb = selectedPath
    .filter((id): id is number => id != null)
    .map((id) => items.find((i) => i.id === id)?.name)
    .filter(Boolean)
    .join(' > ');

  const deepestId = [...selectedPath].reverse().find((id): id is number => id != null);
  const deepestItem = deepestId ? items.find((i) => i.id === deepestId) : null;

  return (
    <div style={{ width: '100%' }}>
      {tiers.length === 1 && tiers[0].items.length > 0 && (
        <div style={{ fontSize: 12, color: 'var(--t-text-muted)', marginBottom: 8 }}>
          Select an item to expand sub-levels
        </div>
      )}
      <div className="tier-view">
        {tiers.map((tier, tierIdx) => (
          <div key={tierIdx} className="tier-column">
            <div className="tier-column-header">{deriveTierLabel(tierIdx)}</div>
            <div className="tier-column-list">
              {tier.items.map((item) => (
                <div key={item.id} className="tier-item-wrapper">
                  {editingId === item.id ? (
                    <div className="tier-item-editing-block">
                      <div className="tier-item tier-item-editing">
                        <input
                          className="tier-inline-input"
                          value={editName}
                          onChange={(e) => setEditName(e.target.value)}
                          onKeyDown={(e) => { if (e.key === 'Enter' && !showContactInfo) handleSaveEdit(); if (e.key === 'Escape') setEditingId(null); }}
                          autoFocus placeholder="Name"
                        />
                        {!showContactInfo && (
                          <>
                            <button className="tier-action-btn" onClick={handleSaveEdit} title="Save">&#10003;</button>
                            <button className="tier-action-btn" onClick={() => setEditingId(null)} title="Cancel">&#10005;</button>
                          </>
                        )}
                      </div>
                      {showContactInfo && (
                        <div className="tier-contact-fields">
                          <input className="tier-inline-input" value={editPhone} onChange={(e) => setEditPhone(e.target.value)} placeholder="Phone" />
                          <input className="tier-inline-input" value={editEmail} onChange={(e) => setEditEmail(e.target.value)} placeholder="Email" />
                          <button className="tier-action-btn" onClick={handleSaveEdit} title="Save">&#10003;</button>
                          <button className="tier-action-btn" onClick={() => setEditingId(null)} title="Cancel">&#10005;</button>
                        </div>
                      )}
                    </div>
                  ) : (
                    <div
                      className={`tier-item ${tier.selectedId === item.id ? 'tier-item-selected' : ''}`}
                      onClick={() => handleSelect(tierIdx, item.id)}
                    >
                      <span className="tier-item-name">{item.name}</span>
                      {childrenOf(item.id).length > 0 && <span className="tier-item-arrow">&#9656;</span>}
                      <div className="tier-item-actions">
                        <button className="tier-action-btn" onClick={(e) => { e.stopPropagation(); setEditingId(item.id); setEditName(item.name); setEditPhone(item.phone || ''); setEditEmail(item.email || ''); }} title="Edit">&#9998;</button>
                        <button className="tier-action-btn tier-action-delete" onClick={(e) => { e.stopPropagation(); handleDelete(item.id); }} title="Delete">&#10005;</button>
                      </div>
                    </div>
                  )}
                </div>
              ))}

              {addingAt === tierIdx ? (
                <div className="tier-item tier-item-adding">
                  <input
                    className="tier-inline-input"
                    placeholder="New item..."
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') handleAdd(tierIdx); if (e.key === 'Escape') setAddingAt(null); }}
                    autoFocus
                  />
                  <button className="tier-action-btn" onClick={() => handleAdd(tierIdx)} title="Add">&#10003;</button>
                  <button className="tier-action-btn" onClick={() => setAddingAt(null)} title="Cancel">&#10005;</button>
                </div>
              ) : (
                <button className="tier-add-btn" onClick={() => { setAddingAt(tierIdx); setNewName(''); }}>+ Add</button>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Breadcrumb + property bar */}
      {breadcrumb && (
        <div style={{ borderTop: '1px solid var(--t-border)', marginTop: 0 }}>
          {/* Top row: breadcrumb + controls */}
          <div className="tier-breadcrumb" style={{ borderBottom: showCustomFields ? '1px solid var(--t-border)' : undefined }}>
            <span style={{ color: 'var(--t-text-muted)', fontSize: 12 }}>Selected: </span>
            <span style={{ fontSize: 12, fontWeight: 500 }}>{breadcrumb}</span>

            {showPriority && deepestItem && (
              <span style={{ marginLeft: 16 }}>
                <label style={{ fontSize: 12, color: 'var(--t-text-muted)', marginRight: 6 }}>Priority:</label>
                <select
                  className="input"
                  style={{ width: 'auto', display: 'inline-block', fontSize: 12, padding: '2px 8px' }}
                  value={deepestItem.default_priority || ''}
                  onChange={(e) => onUpdate(deepestItem.id, { default_priority: e.target.value || null } as any)}
                >
                  <option value="">None</option>
                  <option value="p1">P1 — Urgent</option>
                  <option value="p2">P2 — High</option>
                  <option value="p3">P3 — Medium</option>
                  <option value="p4">P4 — Low</option>
                </select>
              </span>
            )}

            {showTeam && deepestItem && (
              <span style={{ marginLeft: 16 }}>
                <label style={{ fontSize: 12, color: 'var(--t-text-muted)', marginRight: 6 }}>Team:</label>
                <select
                  className="input"
                  style={{ width: 'auto', display: 'inline-block', fontSize: 12, padding: '2px 8px' }}
                  value={deepestItem.team_id || ''}
                  onChange={(e) => onUpdate(deepestItem.id, { team_id: e.target.value ? Number(e.target.value) : null } as any)}
                >
                  <option value="">None</option>
                  {teams.map((t) => (
                    <option key={t.id} value={t.id}>{t.name}</option>
                  ))}
                </select>
              </span>
            )}

            {/* Custom Fields toggle */}
            {deepestItem && (
              <button
                className={`btn btn-sm ${showCustomFields ? 'btn-primary' : 'btn-ghost'}`}
                style={{ marginLeft: 'auto', fontSize: 11, padding: '2px 10px' }}
                onClick={() => setShowCustomFields((v) => !v)}
              >
                Custom Fields
              </button>
            )}
          </div>

          {/* Expanded custom fields panel */}
          {showCustomFields && deepestItem && (
            <div style={{ padding: '12px 16px', background: 'var(--t-panel-alt)' }}>
              <CategoryCustomFields categoryId={deepestItem.id} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
