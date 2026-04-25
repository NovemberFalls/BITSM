import { useEffect, useState, useCallback } from 'react';
import { api } from '../api/client';
import { useUIStore } from '../store/uiStore';
import { pushUrl } from '../utils/url';

interface SetupSteps {
  ai_enabled: boolean;
  categories_configured: boolean;
  team_invited: boolean;
  kb_created: boolean;
  first_ticket: boolean;
}

interface SetupStatusResponse {
  complete: boolean;
  steps: SetupSteps;
}

const DISMISS_KEY = 'setup-checklist-dismissed';

interface ChecklistItem {
  key: keyof SetupSteps;
  title: string;
  description: string;
  action: () => void;
  actionLabel: string;
}

export function SetupChecklist() {
  const [status, setStatus] = useState<SetupStatusResponse | null>(null);
  const [dismissed, setDismissed] = useState(() => localStorage.getItem(DISMISS_KEY) === '1');
  const [loading, setLoading] = useState(true);
  const [enablingAI, setEnablingAI] = useState(false);
  const { setView } = useUIStore();

  const fetchStatus = useCallback(() => {
    api.getSetupStatus()
      .then((data) => {
        setStatus(data);
        // If setup is complete and hasn't been dismissed yet, show the
        // "all done" banner once. The dismissed flag (localStorage) persists
        // so it won't reappear after the user clicks Dismiss.
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    // If dismissed but setup might now be complete, still fetch to check
    fetchStatus();
  }, [fetchStatus]);

  // Re-fetch when the component re-mounts (e.g. navigating back to tickets)
  useEffect(() => {
    const onFocus = () => fetchStatus();
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, [fetchStatus]);

  const navigateTo = (view: string, path: string) => {
    setView(view as any);
    pushUrl(path);
  };

  const handleEnableAI = async () => {
    setEnablingAI(true);
    try {
      await api.enableAI();
      // Re-fetch status after enabling
      const updated = await api.getSetupStatus();
      setStatus(updated);
    } catch {
      // Silently fail — user can retry
    } finally {
      setEnablingAI(false);
    }
  };

  const handleDismiss = () => {
    localStorage.setItem(DISMISS_KEY, '1');
    setDismissed(true);
  };

  if (loading || !status) return null;

  // If setup is fully complete, show a brief success state then let it go
  if (status.complete) {
    if (dismissed) return null;
    return (
      <div className="setup-checklist setup-checklist-complete">
        <div className="setup-checklist-header">
          <div className="setup-checklist-icon">
            <svg width="22" height="22" viewBox="0 0 22 22" fill="none">
              <circle cx="11" cy="11" r="10" stroke="var(--t-success)" strokeWidth="1.5" fill="var(--t-accent-bg)" />
              <path d="M7 11.5l3 3 5.5-5.5" stroke="var(--t-success)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>
          <div>
            <div className="setup-checklist-title">You're all set!</div>
            <div className="setup-checklist-subtitle">Your workspace is fully configured and ready to go.</div>
          </div>
          <button className="btn btn-ghost btn-sm setup-checklist-dismiss" onClick={handleDismiss}>
            Dismiss
          </button>
        </div>
      </div>
    );
  }

  // If dismissed and not complete, still hide — but come back next session
  // (localStorage only persists until they close/reopen)
  if (dismissed) return null;

  const steps = status.steps;

  const items: ChecklistItem[] = [
    {
      key: 'ai_enabled',
      title: 'Enable Atlas AI',
      description: 'Turn on AI-powered ticket triage, agent chat, and knowledge base search.',
      action: handleEnableAI,
      actionLabel: enablingAI ? 'Enabling...' : 'Enable AI',
    },
    {
      key: 'categories_configured',
      title: 'Configure categories',
      description: 'Set up the problem categories your team handles for ticket routing.',
      action: () => navigateTo('admin', '/admin/categories'),
      actionLabel: 'Go to Categories',
    },
    {
      key: 'team_invited',
      title: 'Invite team members',
      description: 'Add agents who will handle tickets and collaborate on issues.',
      action: () => navigateTo('admin', '/admin/users'),
      actionLabel: 'Go to Users',
    },
    {
      key: 'kb_created',
      title: 'Create a KB collection',
      description: 'Upload articles and documents so Atlas can answer questions for your team.',
      action: () => navigateTo('kb', '/kb'),
      actionLabel: 'Go to KB',
    },
    {
      key: 'first_ticket',
      title: 'Submit a test ticket',
      description: 'Create a ticket to see how triage, routing, and AI analysis work end to end.',
      action: () => {
        setView('tickets');
        pushUrl('/tickets');
        setTimeout(() => useUIStore.getState().setCreateTicketOpen(true), 150);
      },
      actionLabel: 'Create Ticket',
    },
  ];

  const completedCount = items.filter((i) => steps[i.key]).length;

  return (
    <div className="setup-checklist">
      <div className="setup-checklist-header">
        <div className="setup-checklist-header-text">
          <div className="setup-checklist-title">Get started with your workspace</div>
          <div className="setup-checklist-subtitle">
            {completedCount} of {items.length} steps complete
          </div>
        </div>
        <button
          className="btn btn-ghost btn-sm setup-checklist-dismiss"
          onClick={handleDismiss}
          title="Dismiss for now"
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M3.5 3.5l7 7M10.5 3.5l-7 7" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
          </svg>
        </button>
      </div>
      <div className="setup-checklist-progress">
        <div
          className="setup-checklist-progress-bar"
          style={{ width: `${(completedCount / items.length) * 100}%` }}
        />
      </div>
      <div className="setup-checklist-items">
        {items.map((item) => {
          const done = steps[item.key];
          return (
            <div key={item.key} className={`setup-checklist-item ${done ? 'setup-checklist-item-done' : ''}`}>
              <div className="setup-checklist-check">
                {done ? (
                  <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
                    <circle cx="9" cy="9" r="8" stroke="var(--t-success)" strokeWidth="1.5" fill="var(--t-accent-bg)" />
                    <path d="M5.5 9.5l2 2 5-5" stroke="var(--t-success)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                ) : (
                  <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
                    <circle cx="9" cy="9" r="8" stroke="var(--t-border-light)" strokeWidth="1.5" />
                  </svg>
                )}
              </div>
              <div className="setup-checklist-item-body">
                <div className="setup-checklist-item-title">{item.title}</div>
                <div className="setup-checklist-item-desc">{item.description}</div>
              </div>
              {!done && (
                <button
                  className="btn btn-ghost btn-sm setup-checklist-action"
                  onClick={item.action}
                  disabled={item.key === 'ai_enabled' && enablingAI}
                >
                  {item.actionLabel}
                </button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
