import { useEffect, useRef, useState } from 'react';
import { api } from '../../api/client';

// ── Sub-components ────────────────────────────────────────

interface SmsConsentProps {
  visible: boolean;
}

function SmsConsentDisclosure({ visible }: SmsConsentProps) {
  if (!visible) return null;
  return (
    <p
      style={{
        fontSize: '0.8rem',
        color: 'var(--t-text-muted)',
        lineHeight: 1.5,
        marginTop: 8,
        marginBottom: 0,
      }}
    >
      By enabling SMS notifications, you consent to receive ticket updates and system
      alerts via text message from BITSM. Message frequency varies. Message and data
      rates may apply. Reply STOP to opt out at any time. See our{' '}
      <a
        href="https://bitsm.io/legal/privacy"
        target="_blank"
        rel="noopener noreferrer"
        style={{ color: 'var(--t-accent)', textDecoration: 'underline' }}
      >
        Privacy Policy
      </a>{' '}
      and{' '}
      <a
        href="https://bitsm.io/legal/terms"
        target="_blank"
        rel="noopener noreferrer"
        style={{ color: 'var(--t-accent)', textDecoration: 'underline' }}
      >
        Terms
      </a>
      .
    </p>
  );
}

// ── Main Component ────────────────────────────────────────

interface UserSettingsProps {
  onClose: () => void;
}

export function UserSettings({ onClose }: UserSettingsProps) {
  const [phoneNumber, setPhoneNumber] = useState('');
  const [smsOptedIn, setSmsOptedIn] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  // Load profile on mount and move focus into the modal
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.getProfile()
      .then((data) => {
        if (cancelled) return;
        setPhoneNumber(data.phone_number ?? '');
        setSmsOptedIn(data.sms_opted_in);
      })
      .catch(() => {
        if (!cancelled) setError('Failed to load profile.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  // Move focus to close button once the panel is rendered
  useEffect(() => {
    closeButtonRef.current?.focus();
  }, [loading]);

  // Trap focus inside the modal and handle Escape
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
        return;
      }
      if (e.key !== 'Tab' || !panelRef.current) return;
      const focusable = panelRef.current.querySelectorAll<HTMLElement>(
        'button, input, a[href], [tabindex]:not([tabindex="-1"])'
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey) {
        if (document.activeElement === first) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  async function handleSave() {
    setError('');
    setSuccess('');
    setSaving(true);
    try {
      const updated = await api.updateProfile({
        phone_number: phoneNumber.trim() || null,
        sms_opted_in: smsOptedIn,
      });
      setPhoneNumber(updated.phone_number ?? '');
      setSmsOptedIn(updated.sms_opted_in);
      setSuccess('Settings saved.');
    } catch (e: any) {
      setError(e?.body?.error || e?.message || 'Failed to save settings.');
    } finally {
      setSaving(false);
    }
  }

  return (
    /* Overlay */
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Account Settings"
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.5)',
        zIndex: 1000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
      onMouseDown={(e) => {
        // Close if clicking the overlay backdrop (not the panel)
        if (e.target === e.currentTarget) onClose();
      }}
    >
      {/* Panel */}
      <div
        ref={panelRef}
        style={{
          background: 'var(--t-bg-surface)',
          border: '1px solid var(--t-border)',
          borderRadius: 8,
          padding: 24,
          minWidth: 400,
          maxWidth: 500,
          width: '100%',
          boxSizing: 'border-box',
          position: 'relative',
        }}
      >
        {/* Header row */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: 20,
          }}
        >
          <h2
            style={{
              margin: 0,
              fontSize: '1rem',
              fontWeight: 700,
              color: 'var(--t-text-primary)',
            }}
          >
            Account Settings
          </h2>
          <button
            ref={closeButtonRef}
            onClick={onClose}
            aria-label="Close account settings"
            style={{
              background: 'none',
              border: 'none',
              cursor: 'pointer',
              color: 'var(--t-text-muted)',
              fontSize: '1.2rem',
              lineHeight: 1,
              padding: '2px 6px',
              borderRadius: 4,
            }}
          >
            &times;
          </button>
        </div>

        {/* Loading state */}
        {loading && (
          <p style={{ color: 'var(--t-text-muted)', fontSize: '0.875rem' }}>
            Loading...
          </p>
        )}

        {/* Form */}
        {!loading && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
            {/* Phone number section */}
            <div>
              <label
                htmlFor="user-settings-phone"
                className="form-label"
                style={{ display: 'block', marginBottom: 6 }}
              >
                SMS Phone Number
              </label>
              <input
                id="user-settings-phone"
                type="tel"
                className="form-input"
                value={phoneNumber}
                onChange={(e) => setPhoneNumber(e.target.value)}
                placeholder="+1 (555) 000-0000"
                autoComplete="tel"
                style={{ width: '100%', boxSizing: 'border-box' }}
              />
              <p
                style={{
                  fontSize: '0.75rem',
                  color: 'var(--t-text-muted)',
                  marginTop: 6,
                  marginBottom: 0,
                  lineHeight: 1.4,
                }}
              >
                Used for SMS ticket notifications. E.164 format recommended (e.g.
                +12223334444).
              </p>
            </div>

            {/* SMS opt-in section */}
            <div>
              <label
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  cursor: 'pointer',
                }}
              >
                <input
                  type="checkbox"
                  id="user-settings-sms-optin"
                  checked={smsOptedIn}
                  onChange={(e) => setSmsOptedIn(e.target.checked)}
                  aria-describedby="sms-consent-disclosure"
                />
                <span
                  style={{
                    fontSize: 13,
                    color: 'var(--t-text-primary)',
                    fontWeight: 600,
                  }}
                >
                  Enable SMS Notifications
                </span>
              </label>

              {/* Consent disclosure — always visible when toggle is on */}
              <div id="sms-consent-disclosure" style={{ marginTop: 8 }}>
                <SmsConsentDisclosure visible={smsOptedIn} />
              </div>
            </div>

            {/* Inline feedback */}
            {error && (
              <p
                role="alert"
                style={{
                  fontSize: '0.8rem',
                  color: 'var(--t-danger)',
                  margin: 0,
                }}
              >
                {error}
              </p>
            )}
            {success && (
              <p
                role="status"
                style={{
                  fontSize: '0.8rem',
                  color: 'var(--t-accent)',
                  margin: 0,
                }}
              >
                {success}
              </p>
            )}

            {/* Save button */}
            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <button
                className="btn btn-primary"
                onClick={handleSave}
                disabled={saving}
                aria-busy={saving}
              >
                {saving ? 'Saving...' : 'Save Settings'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
