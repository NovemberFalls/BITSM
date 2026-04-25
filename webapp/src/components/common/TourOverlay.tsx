import { useEffect, useRef, useState } from 'react';
import { useAuthStore } from '../../store/authStore';

const TOUR_KEY = 'helpdesk_tour_v1';

interface TourStep {
  key: string;
  title: string;
  body: string;
  target?: string;  // CSS selector — if omitted, renders centered
}

function buildSteps(hasAiChat: boolean, canAdmin: boolean): TourStep[] {
  const steps: TourStep[] = [
    {
      key: 'welcome',
      title: 'Welcome to Helpdesk',
      body: "You're set up and ready to go. Let's take a quick look at what you can do here.",
    },
    {
      key: 'tickets',
      title: 'Tickets',
      body: 'Support tickets are the core of the platform. Create, assign, and track issues from open to resolved. AI categorises and routes automatically when enabled.',
      target: '[data-tour="nav-tickets"]',
    },
    {
      key: 'kb',
      title: 'Knowledge Base',
      body: "Your team's articles and guides. This is what Atlas reads when answering questions — keep it current and your AI gets smarter over time.",
      target: '[data-tour="nav-kb"]',
    },
  ];

  if (hasAiChat) {
    steps.push({
      key: 'chat',
      title: 'Atlas AI',
      body: 'Chat directly with Atlas for instant answers. Atlas searches your knowledge base, escalates to a team member when needed, and gets better with every resolved ticket.',
      target: '[data-tour="nav-chat"]',
    });
  }

  if (canAdmin) {
    steps.push({
      key: 'admin',
      title: 'Admin Panel',
      body: 'Manage your team, configure ticket categories, control AI features, and review usage — all from one place.',
      target: '[data-tour="nav-admin"]',
    });
  }

  steps.push({
    key: 'done',
    title: "You're all set",
    body: "That's the tour. Your account representative may reach out to walk you through the platform in detail and help you get fully configured.",
  });

  return steps;
}

interface Rect { top: number; left: number; width: number; height: number; }
const PAD = 7;

export function TourOverlay() {
  const user = useAuthStore((s) => s.user);

  const [active, setActive]   = useState(false);
  const [idx, setIdx]         = useState(0);
  const [rect, setRect]       = useState<Rect | null>(null);
  const rafRef                = useRef<number>(0);

  const hasAiChat = !!window.__APP_CONFIG__?.ai_chat_enabled;
  const canAdmin  = user?.role === 'super_admin' || user?.role === 'tenant_admin';
  const steps     = buildSteps(hasAiChat, canAdmin);
  const step      = steps[idx];
  const isLast    = idx === steps.length - 1;

  // Decide whether to show tour on first load
  useEffect(() => {
    if (!user) return;
    if (user.role === 'super_admin' || user.role === 'end_user') return;
    const storageKey = `${TOUR_KEY}_${user.email}`;
    if (!localStorage.getItem(storageKey)) {
      const t = setTimeout(() => setActive(true), 900);
      return () => clearTimeout(t);
    }
  }, [user]);

  // Track highlighted element position
  useEffect(() => {
    if (!active || !step.target) { setRect(null); return; }
    const measure = () => {
      const el = document.querySelector(step.target!);
      if (el) {
        const r = el.getBoundingClientRect();
        setRect({ top: r.top - PAD, left: r.left - PAD, width: r.width + PAD * 2, height: r.height + PAD * 2 });
      }
    };
    measure();
    rafRef.current = requestAnimationFrame(measure);
    return () => cancelAnimationFrame(rafRef.current);
  }, [active, idx, step.target]);

  const dismiss = () => {
    if (user) localStorage.setItem(`${TOUR_KEY}_${user.email}`, '1');
    setActive(false);
  };

  const next = () => isLast ? dismiss() : setIdx(i => i + 1);
  const back = () => setIdx(i => i - 1);

  if (!active) return null;

  const card = (
    <div style={{
      background: 'var(--bg-primary)',
      border: '1px solid var(--border-color)',
      borderRadius: 10,
      padding: '20px 22px',
      width: 290,
      boxShadow: '0 12px 40px rgba(0,0,0,0.55)',
      userSelect: 'none',
    }}>
      {/* Progress track */}
      <div style={{ display: 'flex', gap: 5, marginBottom: 14 }}>
        {steps.map((_, i) => (
          <div key={i} style={{
            height: 4,
            flex: i === idx ? 3 : 1,
            borderRadius: 2,
            background: i <= idx ? 'var(--color-accent)' : 'var(--border-color)',
            transition: 'flex 0.3s ease, background 0.2s',
          }} />
        ))}
      </div>

      <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 8, letterSpacing: '-0.01em' }}>
        {step.title}
      </div>
      <div style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.65, marginBottom: 20 }}>
        {step.body}
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <button
          onClick={dismiss}
          style={{ fontSize: 12, color: 'var(--text-muted)', background: 'none', border: 'none', cursor: 'pointer', padding: '4px 0', fontFamily: 'inherit' }}
        >
          Skip tour
        </button>
        <div style={{ display: 'flex', gap: 8 }}>
          {idx > 0 && (
            <button className="btn btn-sm btn-ghost" onClick={back}>Back</button>
          )}
          <button className="btn btn-sm btn-primary" onClick={next}>
            {isLast ? 'Finish' : 'Next →'}
          </button>
        </div>
      </div>
    </div>
  );

  // Centered modal (no target element)
  if (!step.target || !rect) {
    return (
      <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.72)', zIndex: 9999, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        {card}
      </div>
    );
  }

  // Spotlight mode
  const tipLeft = Math.min(rect.left + rect.width + 14, window.innerWidth - 310);
  const tipTop  = Math.max(8, Math.min(rect.top + rect.height / 2 - 110, window.innerHeight - 320));

  return (
    <>
      {/* Full-screen click-blocker (behind spotlight) */}
      <div style={{ position: 'fixed', inset: 0, zIndex: 9997 }} />

      {/* Spotlight box — box-shadow creates the darkened surround */}
      <div style={{
        position: 'fixed',
        top:    rect.top,
        left:   rect.left,
        width:  rect.width,
        height: rect.height,
        borderRadius: 7,
        boxShadow: '0 0 0 9999px rgba(0,0,0,0.72)',
        border: '1.5px solid var(--color-accent)',
        zIndex: 9998,
        pointerEvents: 'none',
        transition: 'top 0.3s ease, left 0.3s ease, width 0.3s ease, height 0.3s ease',
      }} />

      {/* Tooltip card */}
      <div style={{ position: 'fixed', top: tipTop, left: tipLeft, zIndex: 9999 }}>
        {card}
      </div>
    </>
  );
}
