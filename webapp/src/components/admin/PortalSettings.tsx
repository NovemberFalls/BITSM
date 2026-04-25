import { useState, useEffect } from 'react';
import { api } from '../../api/client';
import { useAuthStore } from '../../store/authStore';
import type { PortalCard } from '../../types';
import { DEFAULT_PORTAL_CARDS, BACKGROUND_PRESETS } from '../../types';

const ICON_OPTIONS = [
  'alert-circle', 'search', 'book', 'message-circle', 'file-text',
  'users', 'help-circle', 'settings', 'phone', 'mail', 'zap', 'shield',
];

const ACTION_OPTIONS: { value: PortalCard['action']; label: string }[] = [
  { value: 'create_ticket', label: 'Create Ticket' },
  { value: 'my_tickets', label: 'My Tickets' },
  { value: 'kb', label: 'Knowledge Base' },
  { value: 'chat', label: 'AI Chat' },
  { value: 'url', label: 'External URL' },
];

export function PortalSettings() {
  const tenantId = useAuthStore((s) => s.user?.tenant_id);
  const userRole = useAuthStore((s) => s.user?.role);
  const tenantSettings = (window.__APP_CONFIG__ as any)?.tenant_settings || {};

  const [greeting, setGreeting] = useState(tenantSettings.portal_greeting || 'How can we help you today?');
  const [background, setBackground] = useState(tenantSettings.portal_background || 'gradient-indigo');
  const [cards, setCards] = useState<PortalCard[]>(tenantSettings.portal_cards || DEFAULT_PORTAL_CARDS);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [teams, setTeams] = useState<any[]>([]);
  const [allowedDomains, setAllowedDomains] = useState('');
  const [domainsSaving, setDomainsSaving] = useState(false);
  const [domainsSaved, setDomainsSaved] = useState(false);

  useEffect(() => {
    api.listTeams().then(setTeams).catch(() => {});
    // Load allowed_domains from admin-only endpoint (not in window.__APP_CONFIG__)
    if (tenantId) {
      api.getAllowedDomains(tenantId).then((res) => {
        setAllowedDomains(res.allowed_domains || '');
      }).catch(() => {});
    }
  }, [tenantId]);

  const handleSaveDomains = async () => {
    if (!tenantId) return;
    setDomainsSaving(true);
    setDomainsSaved(false);
    try {
      await api.updateTenantSettings(tenantId, { allowed_domains: allowedDomains.trim() });
      setDomainsSaved(true);
      setTimeout(() => setDomainsSaved(false), 3000);
    } catch (err) {
      console.error('Failed to save allowed domains', err);
    } finally {
      setDomainsSaving(false);
    }
  };

  const handleSave = async () => {
    if (!tenantId) return;
    setSaving(true);
    setSaved(false);
    try {
      await api.updateTenantSettings(tenantId, {
        portal_greeting: greeting,
        portal_background: background,
        portal_cards: cards,
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (err) {
      console.error('Failed to save portal settings', err);
    } finally {
      setSaving(false);
    }
  };

  const updateCard = (index: number, updates: Partial<PortalCard>) => {
    setCards((prev) => prev.map((c, i) => i === index ? { ...c, ...updates } : c));
  };

  const removeCard = (index: number) => {
    setCards((prev) => prev.filter((_, i) => i !== index));
  };

  const addCard = () => {
    setCards((prev) => [
      ...prev,
      {
        id: `card-${Date.now()}`,
        title: 'New Card',
        description: 'Card description',
        icon: 'help-circle',
        action: 'url' as const,
        enabled: true,
        sort_order: prev.length,
      },
    ]);
  };

  const moveCard = (index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= cards.length) return;
    setCards((prev) => {
      const next = [...prev];
      [next[index], next[target]] = [next[target], next[index]];
      return next.map((c, i) => ({ ...c, sort_order: i }));
    });
  };

  if (!tenantId) {
    return <div className="portal-settings-empty">Select a tenant to configure portal settings.</div>;
  }

  const tenantSlug = (window.__APP_CONFIG__ as any)?.tenant_slug;

  return (
    <div className="portal-settings">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
        <h3 className="admin-section-title" style={{ margin: 0 }}>Portal Configuration</h3>
        {tenantSlug && (
          <a
            href={`/${tenantSlug}/portal`}
            target="_blank"
            rel="noopener noreferrer"
            className="btn btn-ghost btn-sm"
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
          >
            Preview Portal &#8599;
          </a>
        )}
      </div>
      <p style={{ fontSize: 12, color: 'var(--t-text-muted)', marginBottom: 20 }}>
        Configure the customer portal landing page that end-users see.
      </p>

      {/* Allowed Domains — admin-only access control */}
      {(userRole === 'tenant_admin' || userRole === 'super_admin') && (
        <div style={{ marginBottom: 28, padding: '16px 20px', background: 'var(--t-surface-2)', borderRadius: 8, border: '1px solid var(--t-border)' }}>
          <label className="form-label" style={{ marginBottom: 4 }}>Allowed Email Domains</label>
          <p style={{ fontSize: 12, color: 'var(--t-text-muted)', marginBottom: 12 }}>
            Users from these domains will be auto-provisioned as end-users when they log in via OAuth.
            Enter comma-separated domains (e.g. <code style={{ fontSize: 11, padding: '1px 4px', background: 'var(--t-surface-3)', borderRadius: 3 }}>acme.com,acme-corp.com</code>).
          </p>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input
              className="form-input"
              type="text"
              value={allowedDomains}
              onChange={(e) => setAllowedDomains(e.target.value)}
              placeholder="example.com,example.org"
              style={{ flex: 1 }}
            />
            <button className="btn btn-primary btn-sm" onClick={handleSaveDomains} disabled={domainsSaving}>
              {domainsSaving ? 'Saving...' : 'Save'}
            </button>
            {domainsSaved && <span style={{ fontSize: 12, color: 'var(--t-success)' }}>Saved!</span>}
          </div>
        </div>
      )}

      {/* Greeting */}
      <div className="form-group" style={{ marginBottom: 20 }}>
        <label className="form-label">Greeting Text</label>
        <input
          className="form-input"
          type="text"
          value={greeting}
          onChange={(e) => setGreeting(e.target.value)}
          placeholder="How can we help you today?"
        />
      </div>

      {/* Background preset */}
      <div className="form-group" style={{ marginBottom: 24 }}>
        <label className="form-label">Hero Background</label>
        <div className="portal-bg-picker">
          {BACKGROUND_PRESETS.map((preset) => (
            <button
              key={preset.id}
              className={`portal-bg-swatch portal-hero--${preset.id} ${background === preset.id ? 'active' : ''}`}
              onClick={() => setBackground(preset.id)}
              title={preset.label}
            />
          ))}
        </div>
      </div>

      {/* Cards */}
      <div className="form-group" style={{ marginBottom: 20 }}>
        <label className="form-label">Action Cards</label>
        <div className="portal-card-editor">
          {cards.map((card, i) => (
            <div key={card.id} className="portal-card-editor-row">
              <div className="portal-card-editor-fields">
                <select
                  className="form-input form-select"
                  value={card.icon}
                  onChange={(e) => updateCard(i, { icon: e.target.value })}
                  style={{ width: 120 }}
                >
                  {ICON_OPTIONS.map((icon) => (
                    <option key={icon} value={icon}>{icon}</option>
                  ))}
                </select>
                <input
                  className="form-input"
                  type="text"
                  value={card.title}
                  onChange={(e) => updateCard(i, { title: e.target.value })}
                  placeholder="Title"
                  style={{ flex: 1 }}
                />
                <select
                  className="form-input form-select"
                  value={card.action}
                  onChange={(e) => updateCard(i, { action: e.target.value as PortalCard['action'] })}
                  style={{ width: 140 }}
                >
                  {ACTION_OPTIONS.map((a) => (
                    <option key={a.value} value={a.value}>{a.label}</option>
                  ))}
                </select>
              </div>
              <div className="portal-card-editor-fields">
                <input
                  className="form-input"
                  type="text"
                  value={card.description}
                  onChange={(e) => updateCard(i, { description: e.target.value })}
                  placeholder="Description"
                  style={{ flex: 1 }}
                />
                {card.action === 'url' && (
                  <input
                    className="form-input"
                    type="text"
                    value={card.url || ''}
                    onChange={(e) => updateCard(i, { url: e.target.value })}
                    placeholder="https://..."
                    style={{ width: 200 }}
                  />
                )}
                {card.action === 'create_ticket' && teams.length > 0 && (
                  <select
                    className="form-input form-select"
                    value={card.default_team_id ?? ''}
                    onChange={(e) => updateCard(i, { default_team_id: e.target.value ? Number(e.target.value) : undefined })}
                    style={{ width: 160 }}
                    title="Default team for tickets created from this card"
                  >
                    <option value="">No default team</option>
                    {teams.map((t) => (
                      <option key={t.id} value={t.id}>{t.name}</option>
                    ))}
                  </select>
                )}
              </div>
              <div className="portal-card-editor-actions">
                <label className="portal-card-toggle">
                  <input
                    type="checkbox"
                    checked={card.enabled}
                    onChange={(e) => updateCard(i, { enabled: e.target.checked })}
                  />
                  <span style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>Enabled</span>
                </label>
                <button className="btn btn-ghost btn-xs" onClick={() => moveCard(i, -1)} disabled={i === 0}>&#9650;</button>
                <button className="btn btn-ghost btn-xs" onClick={() => moveCard(i, 1)} disabled={i === cards.length - 1}>&#9660;</button>
                <button className="btn btn-ghost btn-xs" onClick={() => removeCard(i)} style={{ color: 'var(--t-error)' }}>&#10005;</button>
              </div>
            </div>
          ))}
          <button className="btn btn-ghost btn-sm" onClick={addCard} style={{ marginTop: 8 }}>
            + Add Card
          </button>
        </div>
      </div>

      {/* Save */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
          {saving ? 'Saving...' : 'Save Portal Settings'}
        </button>
        {saved && <span style={{ fontSize: 12, color: 'var(--t-success)' }}>Saved!</span>}
      </div>
    </div>
  );
}
