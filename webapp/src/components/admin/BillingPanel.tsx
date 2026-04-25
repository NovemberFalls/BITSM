import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import { useAuthStore } from '../../store/authStore';

interface UsageData {
  tier: string;
  cap_per_user: number | null;
  plan_expires_at: string | null;
  has_subscription: boolean;
  total_cost: number;
  call_count: number;
  cap: number | null;
  pct_used: number;
  over_cap: boolean;
  reset_date: string;
}

interface BYOKKeys {
  anthropic: string | null;
  openai: string | null;
  voyage: string | null;
  resend: string | null;
  twilio_account_sid: string | null;
  twilio_auth_token: string | null;
  twilio_phone_number: string | null;
  elevenlabs: string | null;
}

const AI_PROVIDERS: { key: keyof BYOKKeys; label: string; description: string }[] = [
  {
    key: 'anthropic',
    label: 'Anthropic',
    description: 'Powers Atlas AI (ticket analysis, chat, routing)',
  },
  {
    key: 'voyage',
    label: 'Voyage',
    description: 'Powers knowledge base embeddings (RAG search)',
  },
  {
    key: 'openai',
    label: 'OpenAI',
    description: 'Fallback embeddings (optional, used if Voyage unavailable)',
  },
];

const COMM_PROVIDERS: { key: keyof BYOKKeys; label: string; description: string }[] = [
  {
    key: 'resend',
    label: 'Resend',
    description: 'Email delivery for ticket notifications and invites',
  },
  {
    key: 'elevenlabs',
    label: 'ElevenLabs',
    description: 'Voice AI agents (phone support)',
  },
  {
    key: 'twilio_account_sid',
    label: 'Twilio Account SID',
    description: 'Starts with AC — identifies your Twilio account',
  },
  {
    key: 'twilio_auth_token',
    label: 'Twilio Auth Token',
    description: 'Secret token for Twilio API authentication',
  },
  {
    key: 'twilio_phone_number',
    label: 'Twilio Phone Number',
    description: 'Your Twilio phone number in E.164 format (e.g. +14155551234)',
  },
];

const TIERS = [
  {
    key: 'starter',
    name: 'Starter',
    price: 50,
    api_credits: 15,
    desc: '~500 Haiku AI interactions/seat/month',
    color: 'var(--t-accent)',
  },
  {
    key: 'pro',
    name: 'Pro',
    price: 100,
    api_credits: 30,
    desc: 'Mid-market. Deeper AI usage included.',
    color: '#9b59b6',
  },
  {
    key: 'business',
    name: 'Business',
    price: 150,
    api_credits: 45,
    desc: 'Team scale. High usage included.',
    color: '#e67e22',
  },
  {
    key: 'enterprise',
    name: 'Enterprise BYOK',
    price: 100,
    api_credits: null,
    desc: 'Bring your own API keys. Zero AI COGS.',
    color: '#e74c3c',
  },
];

const TIER_BADGE: Record<string, string> = {
  free: '#888',
  trial: '#f39c12',
  starter: 'var(--t-accent)',
  pro: '#9b59b6',
  business: '#e67e22',
  enterprise: '#e74c3c',
  demo: '#06b6d4',
};

const TIER_LABEL: Record<string, string> = {
  free: 'Free',
  trial: 'Trial',
  starter: 'Starter',
  pro: 'Pro',
  business: 'Business',
  enterprise: 'Enterprise BYOK',
  demo: 'Demo',
};

// ProviderRow: module-scope sub-component for a single BYOK provider row
interface ProviderRowProps {
  providerKey: keyof BYOKKeys;
  label: string;
  description: string;
  maskedValue: string | null;
  inputValue: string;
  showInput: boolean;
  onToggleShow: () => void;
  onInputChange: (value: string) => void;
}

const PLACEHOLDER_MAP: Partial<Record<keyof BYOKKeys, string>> = {
  twilio_account_sid: 'AC...',
  twilio_phone_number: '+1...',
};

function ProviderRow({
  providerKey,
  label,
  description,
  maskedValue,
  inputValue,
  showInput,
  onToggleShow,
  onInputChange,
}: ProviderRowProps) {
  const isConfigured = maskedValue !== null;
  const emptyPlaceholder = PLACEHOLDER_MAP[providerKey] || 'Enter API key...';

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 8,
      padding: '14px 0',
      borderBottom: '1px solid var(--t-border)',
    }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 180 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3 }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--t-text-bright)' }}>{label}</span>
            {isConfigured ? (
              <span style={{
                fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 10,
                background: 'var(--t-success-bg, #1a3a2a)', color: 'var(--t-success, #4caf50)',
                textTransform: 'uppercase', letterSpacing: '0.04em',
              }}>
                Configured
              </span>
            ) : (
              <span style={{
                fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 10,
                background: 'var(--t-bg-secondary)', color: 'var(--t-text-dim)',
                textTransform: 'uppercase', letterSpacing: '0.04em',
              }}>
                Not set
              </span>
            )}
          </div>
          <div style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>{description}</div>
          {isConfigured && (
            <div style={{ fontSize: 11, color: 'var(--t-text-dim)', marginTop: 3, fontFamily: 'monospace' }}>
              {maskedValue}
            </div>
          )}
        </div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexShrink: 0 }}>
          <div style={{ position: 'relative' }}>
            <input
              type={showInput ? 'text' : 'password'}
              value={inputValue}
              onChange={(e) => onInputChange(e.target.value)}
              placeholder={isConfigured ? 'Enter new value to replace...' : emptyPlaceholder}
              aria-label={`${label} API key`}
              style={{
                fontSize: 12, padding: '6px 32px 6px 10px',
                background: 'var(--t-bg-secondary)',
                border: '1px solid var(--t-border)',
                borderRadius: 6,
                color: 'var(--t-text)',
                width: 240,
                outline: 'none',
              }}
            />
            <button
              type="button"
              onClick={onToggleShow}
              aria-label={showInput ? `Hide ${label} key` : `Show ${label} key`}
              style={{
                position: 'absolute', right: 6, top: '50%', transform: 'translateY(-50%)',
                background: 'none', border: 'none', cursor: 'pointer',
                color: 'var(--t-text-muted)', fontSize: 13, padding: 2,
              }}
            >
              {showInput ? '🙈' : '👁'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export function BillingPanel() {
  const [usage, setUsage] = useState<UsageData | null>(null);
  const [loading, setLoading] = useState(true);
  const [upgrading, setUpgrading] = useState<string | null>(null);
  const [portalLoading, setPortalLoading] = useState(false);
  const [billingBanner, setBillingBanner] = useState<'success' | 'cancelled' | null>(null);
  const isSuperAdmin = useAuthStore((s) => s.isSuperAdmin);

  // BYOK state
  const [byokKeys, setByokKeys] = useState<BYOKKeys | null>(null);
  const [byokInputs, setByokInputs] = useState<Record<keyof BYOKKeys, string>>({
    anthropic: '',
    openai: '',
    voyage: '',
    resend: '',
    twilio_account_sid: '',
    twilio_auth_token: '',
    twilio_phone_number: '',
    elevenlabs: '',
  });
  const [byokShowPlain, setByokShowPlain] = useState<Record<keyof BYOKKeys, boolean>>({
    anthropic: false,
    openai: false,
    voyage: false,
    resend: false,
    twilio_account_sid: false,
    twilio_auth_token: false,
    twilio_phone_number: false,
    elevenlabs: false,
  });
  const [byokSaving, setByokSaving] = useState(false);
  const [byokResult, setByokResult] = useState<{
    type: 'success' | 'error';
    message: string;
    validated?: Record<string, boolean>;
  } | null>(null);

  useEffect(() => {
    api.getBillingUsage().then((d) => { setUsage(d); setLoading(false); }).catch(() => setLoading(false));
    // Stripe redirects back with ?billing=success or ?billing=cancelled
    const params = new URLSearchParams(window.location.search);
    const billingParam = params.get('billing');
    if (billingParam === 'success' || billingParam === 'cancelled') {
      setBillingBanner(billingParam);
      // Clean the query param from the URL without reloading
      const url = new URL(window.location.href);
      url.searchParams.delete('billing');
      window.history.replaceState({}, '', url.toString());
    }
  }, []);

  // Load BYOK keys when tier warrants it
  useEffect(() => {
    if (!usage) return;
    const tier = usage.tier;
    const demoMode = (window as any).__APP_CONFIG__?.demo_mode;
    if (tier === 'demo' || tier === 'enterprise' || demoMode) {
      api.getBYOKKeys()
        .then((keys) => setByokKeys(keys))
        .catch(() => setByokKeys({
          anthropic: null,
          openai: null,
          voyage: null,
          resend: null,
          twilio_account_sid: null,
          twilio_auth_token: null,
          twilio_phone_number: null,
          elevenlabs: null,
        }));
    }
  }, [usage]);

  const handleUpgrade = async (tier: string) => {
    setUpgrading(tier);
    try {
      const { url } = await api.createCheckoutSession(tier);
      window.location.href = url;
    } catch {
      setUpgrading(null);
    }
  };

  const handlePortal = async () => {
    setPortalLoading(true);
    try {
      const { url } = await api.openBillingPortal();
      window.location.href = url;
    } catch {
      setPortalLoading(false);
    }
  };

  const handleSaveBYOK = async () => {
    setByokSaving(true);
    setByokResult(null);
    // Build payload: include only non-empty inputs
    const payload: Record<string, string> = {};
    (Object.keys(byokInputs) as Array<keyof BYOKKeys>).forEach((k) => {
      if (byokInputs[k] !== '') {
        payload[k] = byokInputs[k];
      }
    });
    if (Object.keys(payload).length === 0) {
      setByokResult({ type: 'error', message: 'No keys entered. Enter at least one key to save.' });
      setByokSaving(false);
      return;
    }
    try {
      const res = await api.setBYOKKeys(payload);
      // Refresh masked values from server
      const refreshed = await api.getBYOKKeys();
      setByokKeys(refreshed);
      // Clear inputs that were saved
      setByokInputs((prev) => {
        const next = { ...prev };
        (Object.keys(payload) as Array<keyof BYOKKeys>).forEach((k) => {
          next[k] = '';
        });
        return next;
      });
      const validatedKeys = res.validated ? Object.keys(res.validated).filter((k) => res.validated![k]) : [];
      setByokResult({
        type: 'success',
        message: validatedKeys.length > 0
          ? `Keys saved and validated: ${validatedKeys.join(', ')}.`
          : 'Keys saved.',
        validated: res.validated,
      });
    } catch (err: any) {
      const msg = err?.message || 'Failed to save keys.';
      setByokResult({ type: 'error', message: msg });
    } finally {
      setByokSaving(false);
    }
  };

  if (loading) return <div className="audit-empty">Loading billing info...</div>;
  if (!usage) return <div className="audit-empty">Unable to load billing data.</div>;

  const tier = usage.tier;
  const isFreeTier = tier === 'free';
  const isTrialTier = tier === 'trial';
  const isDemoTier = tier === 'demo';
  const isPaid = !isFreeTier && !isTrialTier && !isDemoTier;
  const tierColor = TIER_BADGE[tier] || 'var(--t-text-muted)';
  const capDisplay = usage.cap != null ? `$${usage.cap.toFixed(2)}` : 'Unlimited';
  const spendDisplay = `$${usage.total_cost.toFixed(2)}`;
  const barPct = Math.min(usage.pct_used, 100);

  const demoMode = (window as any).__APP_CONFIG__?.demo_mode;
  const showBYOK = isDemoTier || tier === 'enterprise' || demoMode;
  const trialExpiresAt = (window as any).__APP_CONFIG__?.trial_expires_at;

  const noKeysConfigured =
    byokKeys !== null &&
    (Object.keys(byokKeys) as Array<keyof BYOKKeys>).every((k) => byokKeys[k] === null);

  return (
    <div style={{ maxWidth: 800 }}>

      {/* Stripe return banners */}
      {billingBanner === 'success' && (
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '10px 16px', marginBottom: 16, borderRadius: 8,
          background: 'var(--t-success-bg, #1a3a2a)', color: 'var(--t-success, #4caf50)',
          border: '1px solid var(--t-success, #4caf50)',
        }}>
          <span>Payment successful — your plan has been upgraded.</span>
          <button onClick={() => setBillingBanner(null)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'inherit', fontSize: 16 }}>x</button>
        </div>
      )}
      {billingBanner === 'cancelled' && (
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '10px 16px', marginBottom: 16, borderRadius: 8,
          background: 'var(--t-warning-bg, #3a2a1a)', color: 'var(--t-warning, #f39c12)',
          border: '1px solid var(--t-warning, #f39c12)',
        }}>
          <span>Checkout was cancelled — no charge was made.</span>
          <button onClick={() => setBillingBanner(null)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'inherit', fontSize: 16 }}>x</button>
        </div>
      )}

      {/* Current plan banner */}
      <div className="card" style={{ padding: 20, marginBottom: 24 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
              <span style={{
                fontSize: 11, fontWeight: 700, padding: '3px 10px', borderRadius: 12,
                background: `${tierColor}22`, color: tierColor, textTransform: 'uppercase', letterSpacing: '0.05em',
              }}>
                {TIER_LABEL[tier] || tier}
              </span>
              {usage.plan_expires_at && (
                <span style={{ fontSize: 12, color: 'var(--t-warning)' }}>
                  Trial ends {new Date(usage.plan_expires_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
                </span>
              )}
            </div>
            <div style={{ fontSize: 13, color: 'var(--t-text-muted)' }}>
              {isFreeTier && 'AI features are disabled. Upgrade to restore access.'}
              {isTrialTier && 'Full Starter-tier access during trial. No payment required yet.'}
              {isDemoTier && (
                <>
                  Demo mode — BYOK keys required for AI features.
                  {trialExpiresAt && (
                    <> Data purges on {new Date(trialExpiresAt).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}.</>
                  )}
                </>
              )}
              {isPaid && `Active subscription · resets ${new Date(usage.reset_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`}
            </div>
          </div>
          {usage.has_subscription && !isSuperAdmin() && tier !== 'demo' && (
            <button className="btn btn-ghost btn-sm" onClick={handlePortal} disabled={portalLoading}>
              {portalLoading ? 'Loading...' : 'Manage Subscription ->'}
            </button>
          )}
        </div>

        {/* Usage bar */}
        {usage.cap !== null && (
          <div style={{ marginTop: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--t-text-muted)', marginBottom: 6 }}>
              <span>API usage this month — {spendDisplay} of {capDisplay}</span>
              <span style={{ color: usage.over_cap ? 'var(--t-error)' : undefined }}>
                {usage.pct_used.toFixed(0)}%{usage.over_cap ? ' — cap reached' : ''}
              </span>
            </div>
            <div style={{ height: 6, borderRadius: 3, background: 'var(--t-bg-secondary)', overflow: 'hidden' }}>
              <div style={{
                height: '100%', borderRadius: 3,
                width: `${barPct}%`,
                background: usage.over_cap ? 'var(--t-error)' : barPct > 80 ? 'var(--t-warning)' : 'var(--t-accent)',
                transition: 'width 0.4s ease',
              }} />
            </div>
            <div style={{ fontSize: 11, color: 'var(--t-text-dim)', marginTop: 4 }}>
              {usage.call_count.toLocaleString()} AI calls · resets {new Date(usage.reset_date).toLocaleDateString('en-US', { month: 'long', day: 'numeric' })}
            </div>
          </div>
        )}

        {usage.cap === null && (
          <div style={{ marginTop: 12, fontSize: 12, color: 'var(--t-success)' }}>
            Unlimited — you supply your own API keys (zero AI COGS billed by us)
          </div>
        )}
      </div>

      {/* BYOK Key Management */}
      {showBYOK && (
        <div className="card" style={{ padding: 20, marginBottom: 24 }}>
          <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--t-text-bright)', marginBottom: 4, marginTop: 0 }}>
            API Keys — Bring Your Own Key
          </h3>
          <p style={{ fontSize: 12, color: 'var(--t-text-muted)', marginBottom: 16, marginTop: 0 }}>
            Enter your provider API keys below. Keys are encrypted at rest and never returned in plaintext.
            Leave a field empty to keep the existing key unchanged. Enter a blank value only if you intend to remove a key.
          </p>

          {noKeysConfigured && (
            <div style={{
              padding: '10px 14px', marginBottom: 16, borderRadius: 6,
              background: 'var(--t-warning-bg, #3a2a1a)', color: 'var(--t-warning, #f39c12)',
              border: '1px solid var(--t-warning, #f39c12)', fontSize: 12,
            }}>
              Configure your API keys to enable AI and communications features.
            </div>
          )}

          <div role="list" aria-label="API key providers">
            {byokKeys === null ? (
              <div style={{ fontSize: 12, color: 'var(--t-text-muted)', padding: '12px 0' }}>Loading key status...</div>
            ) : (
              <>
                {AI_PROVIDERS.map((provider) => (
                  <div key={provider.key} role="listitem">
                    <ProviderRow
                      providerKey={provider.key}
                      label={provider.label}
                      description={provider.description}
                      maskedValue={byokKeys[provider.key]}
                      inputValue={byokInputs[provider.key]}
                      showInput={byokShowPlain[provider.key]}
                      onToggleShow={() =>
                        setByokShowPlain((prev) => ({ ...prev, [provider.key]: !prev[provider.key] }))
                      }
                      onInputChange={(val) =>
                        setByokInputs((prev) => ({ ...prev, [provider.key]: val }))
                      }
                    />
                  </div>
                ))}
                <div style={{
                  fontSize: 11, fontWeight: 600, color: 'var(--t-text-dim)',
                  textTransform: 'uppercase', letterSpacing: '0.06em',
                  marginTop: 12, marginBottom: 4, paddingTop: 12,
                  borderTop: '1px solid var(--t-border)',
                }}>
                  Communications
                </div>
                {COMM_PROVIDERS.map((provider) => (
                  <div key={provider.key} role="listitem">
                    <ProviderRow
                      providerKey={provider.key}
                      label={provider.label}
                      description={provider.description}
                      maskedValue={byokKeys[provider.key]}
                      inputValue={byokInputs[provider.key]}
                      showInput={byokShowPlain[provider.key]}
                      onToggleShow={() =>
                        setByokShowPlain((prev) => ({ ...prev, [provider.key]: !prev[provider.key] }))
                      }
                      onInputChange={(val) =>
                        setByokInputs((prev) => ({ ...prev, [provider.key]: val }))
                      }
                    />
                  </div>
                ))}
              </>
            )}
          </div>

          {/* Result feedback */}
          {byokResult && (
            <div style={{
              marginTop: 14, padding: '10px 14px', borderRadius: 6, fontSize: 12,
              background: byokResult.type === 'success'
                ? 'var(--t-success-bg, #1a3a2a)'
                : 'var(--t-error-bg, #3a1a1a)',
              color: byokResult.type === 'success'
                ? 'var(--t-success, #4caf50)'
                : 'var(--t-error, #e74c3c)',
              border: `1px solid ${byokResult.type === 'success' ? 'var(--t-success, #4caf50)' : 'var(--t-error, #e74c3c)'}`,
            }} role="alert">
              {byokResult.message}
            </div>
          )}

          <div style={{ marginTop: 16, display: 'flex', justifyContent: 'flex-end' }}>
            <button
              className="btn btn-primary btn-sm"
              onClick={handleSaveBYOK}
              disabled={byokSaving || byokKeys === null}
              aria-busy={byokSaving}
            >
              {byokSaving ? 'Saving...' : 'Save Keys'}
            </button>
          </div>
        </div>
      )}

      {/* Plan cards */}
      {!isSuperAdmin() && tier !== 'demo' && !demoMode && (
        <>
          <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--t-text-bright)', marginBottom: 16 }}>
            {isPaid ? 'Change Plan' : 'Choose a Plan'}
          </h3>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 12 }}>
            {TIERS.map((t) => {
              const isCurrent = tier === t.key;
              return (
                <div key={t.key} className="card" style={{
                  padding: 16, border: isCurrent ? `2px solid ${t.color}` : undefined,
                  display: 'flex', flexDirection: 'column', gap: 8,
                }}>
                  <div style={{ fontSize: 13, fontWeight: 700, color: t.color }}>{t.name}</div>
                  <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--t-text-bright)' }}>
                    ${t.price}<span style={{ fontSize: 12, fontWeight: 400, color: 'var(--t-text-muted)' }}>/seat/mo</span>
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--t-text-muted)', lineHeight: 1.5, flex: 1 }}>
                    {t.api_credits != null
                      ? <><strong>${t.api_credits}</strong> API credits/seat/mo<br /></>
                      : <><strong>Unlimited</strong> (BYOK)<br /></>
                    }
                    {t.desc}
                  </div>
                  {isCurrent ? (
                    <div style={{ fontSize: 11, color: t.color, fontWeight: 600 }}>Current plan</div>
                  ) : (
                    <button
                      className="btn btn-primary btn-sm"
                      style={{ background: t.color, borderColor: t.color, fontSize: 11 }}
                      disabled={upgrading === t.key}
                      onClick={() => handleUpgrade(t.key)}
                    >
                      {upgrading === t.key ? 'Redirecting...' : 'Select'}
                    </button>
                  )}
                </div>
              );
            })}
          </div>

          <div style={{ marginTop: 16, fontSize: 11, color: 'var(--t-text-dim)', lineHeight: 1.6 }}>
            Seats = active agent/admin users. Billing is per seat, per month. Adding users increases your plan total and API credit budget proportionally.
            Enterprise BYOK triggered automatically when your API spend exceeds the Business cap.
          </div>
        </>
      )}
    </div>
  );
}
