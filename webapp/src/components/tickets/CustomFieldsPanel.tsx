/**
 * CustomFieldsPanel — renders the custom fields section on a ticket.
 *
 * Props:
 *   fields     — definitions with current_value already attached (from GET /api/tickets/:id)
 *   ticketId   — used to PATCH values on change
 *   readOnly   — true for end_user portal (still shows values, no edit)
 */
import { useState } from 'react';
import { api } from '../../api/client';
import type { CustomFieldDefinition } from '../../types';

interface Props {
  fields: CustomFieldDefinition[];
  ticketId: number;
  readOnly?: boolean;
  onUpdated?: () => void;
  highlightFields?: string[];
}

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '5px 8px',
  background: 'var(--t-bg)',
  border: '1px solid var(--t-border)',
  borderRadius: 4,
  color: 'var(--t-text)',
  fontSize: 12,
  outline: 'none',
  boxSizing: 'border-box',
};

const textareaStyle: React.CSSProperties = {
  ...inputStyle,
  resize: 'vertical',
  fontFamily: 'inherit',
  lineHeight: 1.5,
};

const selectStyle: React.CSSProperties = {
  ...inputStyle,
  cursor: 'pointer',
};

export function CustomFieldsPanel({ fields, ticketId, readOnly = false, onUpdated, highlightFields = [] }: Props) {
  const [saving, setSaving] = useState<number | null>(null);
  const [localValues, setLocalValues] = useState<Record<number, any>>(() => {
    const init: Record<number, any> = {};
    for (const f of fields) {
      init[f.id] = f.current_value ?? (f.field_type === 'multi_select' ? [] : f.field_type === 'checkbox' ? false : '');
    }
    return init;
  });

  if (!fields.length) return null;

  const handleChange = (fieldId: number, value: any) => {
    setLocalValues((prev) => ({ ...prev, [fieldId]: value }));
  };

  const handleBlur = async (field: CustomFieldDefinition) => {
    if (readOnly) return;
    const value = localValues[field.id];
    setSaving(field.id);
    try {
      await api.updateTicket(ticketId, {
        custom_fields: { [field.field_key]: value },
      });
      onUpdated?.();
    } catch {}
    setSaving(null);
  };

  const handleCheckboxChange = async (field: CustomFieldDefinition, checked: boolean) => {
    if (readOnly) return;
    setLocalValues((prev) => ({ ...prev, [field.id]: checked }));
    setSaving(field.id);
    try {
      await api.updateTicket(ticketId, {
        custom_fields: { [field.field_key]: checked },
      });
      onUpdated?.();
    } catch {}
    setSaving(null);
  };

  const handleSelectChange = async (field: CustomFieldDefinition, value: any) => {
    if (readOnly) return;
    setLocalValues((prev) => ({ ...prev, [field.id]: value }));
    setSaving(field.id);
    try {
      await api.updateTicket(ticketId, {
        custom_fields: { [field.field_key]: value },
      });
      onUpdated?.();
    } catch {}
    setSaving(null);
  };

  const handleMultiToggle = async (field: CustomFieldDefinition, optValue: string) => {
    if (readOnly) return;
    const cur: string[] = Array.isArray(localValues[field.id]) ? localValues[field.id] : [];
    const next = cur.includes(optValue) ? cur.filter((v) => v !== optValue) : [...cur, optValue];
    setLocalValues((prev) => ({ ...prev, [field.id]: next }));
    setSaving(field.id);
    try {
      await api.updateTicket(ticketId, {
        custom_fields: { [field.field_key]: next },
      });
      onUpdated?.();
    } catch {}
    setSaving(null);
  };

  return (
    <div style={{ marginTop: 16 }}>
      <div style={{
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: '0.08em',
        textTransform: 'uppercase',
        color: 'var(--t-text-muted)',
        marginBottom: 8,
      }}>
        Custom Fields
      </div>

      {fields.map((field) => {
        const val = localValues[field.id];
        const isSaving = saving === field.id;
        const isEmpty = val === '' || val === null || val === undefined || (Array.isArray(val) && val.length === 0);
        const showRequired = (field.is_required_to_create || field.is_required_to_close) && isEmpty && !readOnly;
        const isHighlighted = highlightFields.includes(field.name);

        return (
          <div key={field.id} style={{
            marginBottom: 10,
            padding: '8px 10px',
            background: isHighlighted ? 'rgba(239,68,68,0.1)' : showRequired ? 'color-mix(in srgb, var(--t-warning) 8%, transparent)' : 'var(--t-panel-alt)',
            border: `1px solid ${isHighlighted ? 'rgba(239,68,68,0.6)' : showRequired ? 'var(--t-warning)' : 'var(--t-border)'}`,
            borderRadius: 6,
            transition: 'border-color 0.3s, background 0.3s',
          }}>
            {/* Label row */}
            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              marginBottom: 5,
              flexWrap: 'wrap',
            }}>
              <span style={{ fontSize: 12, fontWeight: 500, color: isHighlighted ? 'rgba(239,68,68,1)' : 'var(--t-text)' }}>
                {field.name}
                {(field.is_required_to_create || field.is_required_to_close) && (
                  <span style={{ color: isHighlighted ? 'rgba(239,68,68,1)' : 'var(--t-warning)', marginLeft: 2 }}>*</span>
                )}
              </span>
              {isHighlighted && (
                <span style={{ fontSize: 10, color: 'rgba(239,68,68,1)', fontWeight: 500 }}>Required before submission</span>
              )}

              {field.is_required_to_close && (
                <span style={{
                  fontSize: 10,
                  padding: '1px 5px',
                  borderRadius: 3,
                  background: 'color-mix(in srgb, var(--t-accent) 15%, transparent)',
                  color: 'var(--t-accent)',
                  border: '1px solid color-mix(in srgb, var(--t-accent) 30%, transparent)',
                  fontWeight: 500,
                  whiteSpace: 'nowrap',
                }}>
                  Req. Close
                </span>
              )}

              {field.is_required_to_create && (
                <span style={{
                  fontSize: 10,
                  padding: '1px 5px',
                  borderRadius: 3,
                  background: 'color-mix(in srgb, var(--t-warning) 15%, transparent)',
                  color: 'var(--t-warning)',
                  border: '1px solid color-mix(in srgb, var(--t-warning) 30%, transparent)',
                  fontWeight: 500,
                  whiteSpace: 'nowrap',
                }}>
                  Req. Create
                </span>
              )}

              {isSaving && (
                <span style={{
                  width: 6,
                  height: 6,
                  borderRadius: '50%',
                  background: 'var(--t-accent)',
                  display: 'inline-block',
                  animation: 'pulse 1s infinite',
                  marginLeft: 'auto',
                }} title="Saving…" />
              )}
            </div>

            {field.description && (
              <div style={{
                fontSize: 11,
                color: 'var(--t-text-muted)',
                marginBottom: 5,
                lineHeight: 1.4,
              }}>
                {field.description}
              </div>
            )}

            {/* Input */}
            {field.field_type === 'text' && (
              <input
                style={inputStyle}
                value={val ?? ''}
                onChange={(e) => handleChange(field.id, e.target.value)}
                onBlur={() => handleBlur(field)}
                disabled={readOnly}
                placeholder={readOnly ? '—' : 'Enter value'}
              />
            )}

            {field.field_type === 'textarea' && (
              <textarea
                style={textareaStyle}
                rows={3}
                value={val ?? ''}
                onChange={(e) => handleChange(field.id, e.target.value)}
                onBlur={() => handleBlur(field)}
                disabled={readOnly}
                placeholder={readOnly ? '—' : 'Enter value'}
              />
            )}

            {field.field_type === 'number' && (
              <input
                style={inputStyle}
                type="number"
                value={val ?? ''}
                onChange={(e) => handleChange(field.id, e.target.value === '' ? '' : Number(e.target.value))}
                onBlur={() => handleBlur(field)}
                disabled={readOnly}
                placeholder={readOnly ? '—' : '0'}
              />
            )}

            {field.field_type === 'date' && (
              <input
                style={inputStyle}
                type="date"
                value={val ?? ''}
                onChange={(e) => handleChange(field.id, e.target.value)}
                onBlur={() => handleBlur(field)}
                disabled={readOnly}
              />
            )}

            {field.field_type === 'url' && (
              <input
                style={inputStyle}
                type="url"
                value={val ?? ''}
                onChange={(e) => handleChange(field.id, e.target.value)}
                onBlur={() => handleBlur(field)}
                disabled={readOnly}
                placeholder={readOnly ? '—' : 'https://'}
              />
            )}

            {field.field_type === 'checkbox' && (
              <label style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                cursor: readOnly ? 'default' : 'pointer',
                fontSize: 12,
                color: 'var(--t-text)',
              }}>
                <input
                  type="checkbox"
                  checked={!!val}
                  onChange={(e) => handleCheckboxChange(field, e.target.checked)}
                  disabled={readOnly}
                  style={{ accentColor: 'var(--t-accent)', width: 14, height: 14 }}
                />
                {val ? 'Yes' : 'No'}
              </label>
            )}

            {field.field_type === 'select' && (
              <select
                style={selectStyle}
                value={val ?? ''}
                onChange={(e) => handleSelectChange(field, e.target.value || null)}
                disabled={readOnly}
              >
                <option value="">— Select —</option>
                {(field.options || []).map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            )}

            {field.field_type === 'multi_select' && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
                {(field.options || []).map((o) => {
                  const checked = Array.isArray(val) && val.includes(o.value);
                  return (
                    <button
                      key={o.value}
                      type="button"
                      onClick={() => !readOnly && handleMultiToggle(field, o.value)}
                      disabled={readOnly}
                      style={{
                        padding: '3px 9px',
                        borderRadius: 20,
                        fontSize: 11,
                        cursor: readOnly ? 'default' : 'pointer',
                        border: `1px solid ${checked ? 'var(--t-accent)' : 'var(--t-border)'}`,
                        background: checked
                          ? 'color-mix(in srgb, var(--t-accent) 18%, transparent)'
                          : 'var(--t-bg)',
                        color: checked ? 'var(--t-accent)' : 'var(--t-text-muted)',
                        fontWeight: checked ? 500 : 400,
                        transition: 'all 0.15s',
                      }}
                    >
                      {o.label}
                    </button>
                  );
                })}
              </div>
            )}

            {showRequired && (
              <div style={{
                marginTop: 4,
                fontSize: 11,
                color: 'var(--t-warning)',
              }}>
                {field.is_required_to_create ? 'Required before submission' : 'Required before closing'}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
