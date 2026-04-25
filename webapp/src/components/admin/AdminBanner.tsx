import { useState, useCallback } from 'react';

interface AdminBannerProps {
  id: string;
  title: string;
  children: React.ReactNode;
  /** Increment to force re-check visibility (set from parent on reset) */
  generation?: number;
}

const STORAGE_KEY = 'bitsm_dismissed_banners';

function getDismissed(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]'));
  } catch { return new Set(); }
}

function dismiss(id: string) {
  const set = getDismissed();
  set.add(id);
  localStorage.setItem(STORAGE_KEY, JSON.stringify([...set]));
}

export function resetAllBanners() {
  localStorage.removeItem(STORAGE_KEY);
}

export function AdminBanner({ id, title, children, generation }: AdminBannerProps) {
  const [visible, setVisible] = useState(() => !getDismissed().has(id));

  // Re-show when generation changes (parent triggered reset)
  const [lastGen, setLastGen] = useState(generation ?? 0);
  if ((generation ?? 0) !== lastGen) {
    setLastGen(generation ?? 0);
    if (!getDismissed().has(id)) setVisible(true);
  }

  if (!visible) return null;

  return (
    <div style={{
      margin: '0 0 16px 0',
      padding: '14px 18px',
      background: 'color-mix(in srgb, var(--t-accent) 6%, var(--t-panel))',
      border: '1px solid color-mix(in srgb, var(--t-accent) 20%, var(--t-border))',
      borderRadius: 8,
      position: 'relative',
    }}>
      <button
        onClick={() => { dismiss(id); setVisible(false); }}
        style={{
          position: 'absolute', top: 10, right: 12,
          background: 'none', border: 'none', cursor: 'pointer',
          color: 'var(--t-text-muted)', fontSize: 14, lineHeight: 1,
          padding: '2px 4px',
        }}
        title="Dismiss"
      >
        &times;
      </button>
      <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--t-text-bright)', marginBottom: 6 }}>{title}</div>
      <div style={{ fontSize: 12, lineHeight: 1.5, color: 'var(--t-text)', maxWidth: 720 }}>
        {children}
      </div>
    </div>
  );
}
