import { useEffect, useRef, useState, useCallback } from 'react';
import { useAuthStore } from '../store/authStore';

const ACTIVITY_EVENTS = ['mousemove', 'keydown', 'click', 'scroll'] as const;

function getTimeouts() {
  const minutes = window.__APP_CONFIG__?.idle_timeout_minutes ?? 60;
  const limitMs = minutes * 60 * 1000;
  // Warn 5 min before, or 25% of the timeout — whichever is shorter
  const warnOffset = Math.min(5 * 60 * 1000, limitMs * 0.25);
  const warnMs = limitMs - warnOffset;
  // Keepalive ping interval: 1/3 of the timeout (e.g. every 20 min for 60 min timeout)
  const pingIntervalMs = Math.max(30_000, Math.floor(limitMs / 3));
  return { limitMs, warnMs, warnOffset, pingIntervalMs };
}

export function IdleTimeoutModal() {
  const user = useAuthStore((s) => s.user);

  const { warnOffset } = getTimeouts();
  const [showModal, setShowModal]     = useState(false);
  const [countdown, setCountdown]     = useState(Math.floor(warnOffset / 1000));
  const [pinging, setPinging]         = useState(false);

  // Use a ref for the raw idle-start timestamp — avoids re-renders on every activity event
  const idleStartRef    = useRef<number>(Date.now());
  const warnTimerRef    = useRef<ReturnType<typeof setTimeout> | null>(null);
  const logoutTimerRef  = useRef<ReturnType<typeof setTimeout> | null>(null);
  const countdownRef    = useRef<ReturnType<typeof setInterval> | null>(null);
  const lastPingRef     = useRef<number>(0);

  const doLogout = useCallback(() => {
    // Clear any running timers before redirecting
    if (warnTimerRef.current)   clearTimeout(warnTimerRef.current);
    if (logoutTimerRef.current) clearTimeout(logoutTimerRef.current);
    if (countdownRef.current)   clearInterval(countdownRef.current);
    window.location.href = '/login?reason=timeout';
  }, []);

  const scheduleTimers = useCallback(() => {
    const { warnMs, warnOffset } = getTimeouts();
    if (warnTimerRef.current)   clearTimeout(warnTimerRef.current);
    if (logoutTimerRef.current) clearTimeout(logoutTimerRef.current);
    if (countdownRef.current)   clearInterval(countdownRef.current);

    warnTimerRef.current = setTimeout(() => {
      setShowModal(true);
      setCountdown(Math.floor(warnOffset / 1000));

      // Countdown tick
      countdownRef.current = setInterval(() => {
        setCountdown((c) => {
          if (c <= 1) {
            if (countdownRef.current) clearInterval(countdownRef.current);
            return 0;
          }
          return c - 1;
        });
      }, 1000);

      // Hard logout at full timeout
      logoutTimerRef.current = setTimeout(doLogout, warnOffset);
    }, warnMs);
  }, [doLogout]);

  // Ping server at most once per pingInterval when user is genuinely active.
  // Does NOT fire when idle — intentional, so the server can detect inactivity.
  const maybePingServer = useCallback(() => {
    const { pingIntervalMs } = getTimeouts();
    const now = Date.now();
    if (now - lastPingRef.current > pingIntervalMs) {
      lastPingRef.current = now;
      const csrf = window.__APP_CONFIG__?.csrf_token;
      fetch('/ping', { method: 'POST', headers: csrf ? { 'X-CSRF-Token': csrf } : {} })
        .then((res) => {
          if (res.status === 401 || res.redirected) {
            window.location.href = '/login?reason=timeout';
          }
        })
        .catch(() => {});
    }
  }, []);

  const resetIdle = useCallback(() => {
    idleStartRef.current = Date.now();
    maybePingServer();
    // Only reset timers if the modal isn't currently showing
    setShowModal((open) => {
      if (!open) scheduleTimers();
      return open;
    });
  }, [scheduleTimers, maybePingServer]);

  useEffect(() => {
    if (!user) return;

    scheduleTimers();

    ACTIVITY_EVENTS.forEach((ev) =>
      window.addEventListener(ev, resetIdle, { passive: true })
    );

    return () => {
      if (warnTimerRef.current)   clearTimeout(warnTimerRef.current);
      if (logoutTimerRef.current) clearTimeout(logoutTimerRef.current);
      if (countdownRef.current)   clearInterval(countdownRef.current);
      ACTIVITY_EVENTS.forEach((ev) => window.removeEventListener(ev, resetIdle));
    };
  }, [user, scheduleTimers, resetIdle]);

  const handleStay = async () => {
    setPinging(true);
    try {
      const csrfToken = window.__APP_CONFIG__?.csrf_token;
      const res = await fetch('/ping', { method: 'POST', headers: csrfToken ? { 'X-CSRF-Token': csrfToken } : {} });
      if (res.status === 401 || res.redirected) {
        doLogout();
        return;
      }
      // Success — dismiss and restart timers
      if (countdownRef.current) clearInterval(countdownRef.current);
      if (logoutTimerRef.current) clearTimeout(logoutTimerRef.current);
      setShowModal(false);
      setCountdown(300);
      idleStartRef.current = Date.now();
      scheduleTimers();
    } catch {
      // Network error — assume session still valid, dismiss and retry
      if (countdownRef.current) clearInterval(countdownRef.current);
      if (logoutTimerRef.current) clearTimeout(logoutTimerRef.current);
      setShowModal(false);
      setCountdown(300);
      idleStartRef.current = Date.now();
      scheduleTimers();
    } finally {
      setPinging(false);
    }
  };

  if (!showModal) return null;

  const mins = Math.floor(countdown / 60);
  const secs = countdown % 60;
  const countdownLabel = countdown > 0
    ? `${mins}:${String(secs).padStart(2, '0')}`
    : 'Session expired';

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Session expiring soon"
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.65)',
        backdropFilter: 'blur(2px)',
        zIndex: 10000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <div style={{
        background: 'var(--t-panel)',
        border: '1px solid var(--t-border)',
        borderRadius: 'var(--radius)',
        padding: '32px 28px',
        width: 360,
        maxWidth: 'calc(100vw - 32px)',
        boxShadow: '0 16px 48px rgba(0,0,0,0.6)',
      }}>
        {/* Icon + heading */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
          <div style={{
            width: 36, height: 36, borderRadius: '50%',
            background: 'rgba(var(--accent-rgb, 99, 179, 237), 0.12)',
            border: '1px solid var(--t-border)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 18, flexShrink: 0,
          }}>
            &#9203;
          </div>
          <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--t-text-bright)' }}>
            Session expiring
          </div>
        </div>

        <p style={{ fontSize: 13, color: 'var(--t-text-muted)', lineHeight: 1.6, margin: '0 0 20px' }}>
          You've been inactive for a while. Your session will end automatically in:
        </p>

        {/* Countdown */}
        <div style={{
          textAlign: 'center',
          fontSize: 36,
          fontWeight: 700,
          letterSpacing: '-0.02em',
          color: countdown <= 60 ? 'var(--t-text-danger, #e74c3c)' : 'var(--t-text-bright)',
          margin: '0 0 24px',
          fontVariantNumeric: 'tabular-nums',
          transition: 'color 0.3s',
        }}>
          {countdownLabel}
        </div>

        {/* Actions */}
        <div style={{ display: 'flex', gap: 10 }}>
          <button
            className="btn btn-primary"
            style={{ flex: 1 }}
            onClick={handleStay}
            disabled={pinging || countdown === 0}
          >
            {pinging ? 'Checking...' : 'Stay signed in'}
          </button>
          <button
            className="btn btn-ghost"
            style={{ flex: 1 }}
            onClick={doLogout}
          >
            Sign out
          </button>
        </div>
      </div>
    </div>
  );
}
