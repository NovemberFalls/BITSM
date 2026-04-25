import { useEffect, useState } from 'react';
import { useAuthStore } from './store/authStore';
import { useUIStore, type View } from './store/uiStore';
import { useThemeStore } from './store/themeStore';
import { useTicketStore } from './store/ticketStore';
import { Sidebar } from './components/layout/Sidebar';
import { Topbar } from './components/layout/Topbar';
import { TicketBoard } from './components/tickets/TicketBoard';
import { CreateTicketModal } from './components/tickets/CreateTicketModal';
import { KnowledgeBase } from './components/kb/KnowledgeBase';
import { ChatPanel } from './components/ai/ChatPanel';
import { AdminPanel } from './components/admin/AdminPanel';
import { AuditQueue } from './components/admin/AuditQueue';
import { Reports } from './components/reports/Reports';
import { AutomationList } from './components/automations/AutomationList';
import { AutomationBuilder } from './components/automations/AutomationBuilder';
import { CustomerPortal } from './components/portal/CustomerPortal';
import { SprintManager } from './components/sprints/SprintManager';
import { HexGrid } from './components/common/HexGrid';
import { TourOverlay } from './components/common/TourOverlay';
import { IdleTimeoutModal } from './components/IdleTimeoutModal';
import { stripSlug, replaceUrl, pushUrl } from './utils/url';
import type { AppConfig } from './types';

declare global {
  interface Window {
    __APP_CONFIG__: AppConfig;
  }
}

/** Format an ISO date string to a readable short form, e.g. "Apr 29, 2026" */
function formatExpiryDate(iso: string | null | undefined): string {
  if (!iso) return 'soon';
  try {
    return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  } catch {
    return 'soon';
  }
}

interface DemoBannerProps {
  trialExpiresAt: string | null | undefined;
  byokConfigured: boolean;
}

function DemoBanner({ trialExpiresAt, byokConfigured }: DemoBannerProps) {
  const [dismissed, setDismissed] = useState(false);

  if (dismissed) return null;

  const expiryLabel = formatExpiryDate(trialExpiresAt);

  return (
    <div
      role="banner"
      aria-label="Demo instance notice"
      style={{
        width: '100%',
        background: 'rgba(6, 182, 212, 0.08)',
        border: 'none',
        borderBottom: '1px solid rgba(6, 182, 212, 0.2)',
        padding: '10px 16px',
        fontSize: '12px',
        color: 'var(--t-text-muted)',
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'space-between',
        gap: '12px',
        flexShrink: 0,
      }}
    >
      <div>
        <span>This is a demo instance. Your data will be purged on </span>
        <span style={{ color: '#06b6d4', fontWeight: 600 }}>{expiryLabel}</span>
        <span>. </span>
        <a
          href="https://github.com/NovemberFalls/bitsm"
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: '#06b6d4', textDecoration: 'underline' }}
        >
          Deploy your own
        </a>
        <span> from the repo.</span>
        {!byokConfigured && (
          <div style={{ marginTop: '4px' }}>
            Configure your API keys in{' '}
            <span
              role="link"
              tabIndex={0}
              aria-label="Navigate to Settings, Billing"
              onClick={() => {
                useUIStore.getState().setView('admin');
                pushUrl('/admin/billing');
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  useUIStore.getState().setView('admin');
                  pushUrl('/admin/billing');
                }
              }}
              style={{ color: '#06b6d4', fontWeight: 600, cursor: 'pointer', textDecoration: 'underline' }}
            >
              Settings &rarr; Billing
            </span>
            {' '}to enable AI features.
          </div>
        )}
      </div>
      <button
        onClick={() => setDismissed(true)}
        aria-label="Dismiss demo notice"
        style={{
          background: 'none',
          border: 'none',
          cursor: 'pointer',
          color: 'var(--t-text-muted)',
          fontSize: '16px',
          lineHeight: 1,
          padding: '0 2px',
          flexShrink: 0,
        }}
      >
        &times;
      </button>
    </div>
  );
}

const PATH_TO_VIEW: Record<string, View> = {
  '/': 'tickets',
  '/tickets': 'tickets',
  '/kb': 'kb',
  '/chat': 'chat',
  '/admin': 'admin',
  '/audit': 'audit',
  '/reports': 'reports',
  '/automations': 'automations',
  '/sprints': 'sprints',
  '/portal': 'portal',
};

function pathToView(rawPath: string): View {
  const path = stripSlug(rawPath);
  // Handle portal paths
  if (path.startsWith('/portal')) return 'portal';
  if (path.startsWith('/tickets')) return 'tickets';
  if (path.startsWith('/audit')) return 'audit';
  if (path.startsWith('/reports')) return 'reports';
  if (path.startsWith('/automations')) return 'automations';
  if (path.startsWith('/sprints')) return 'sprints';
  if (path.startsWith('/admin')) return 'admin';
  if (path.startsWith('/kb')) return 'kb';
  if (path.startsWith('/chat')) return 'chat';
  return PATH_TO_VIEW[path] || 'tickets';
}

function getTicketIdFromPath(rawPath: string): number | null {
  const path = stripSlug(rawPath);
  // /tickets/:id
  const ticketMatch = path.match(/^\/tickets\/(\d+)$/);
  if (ticketMatch) return parseInt(ticketMatch[1], 10);
  // /sprints/:sprintId/items/:id or /sprints/items/:id
  const sprintItemMatch = path.match(/\/sprints\/(?:\d+\/)?items\/(\d+)$/);
  if (sprintItemMatch) return parseInt(sprintItemMatch[1], 10);
  return null;
}

/** Read view mode (board/list) from URL query params */
function getViewModeFromURL(): 'board' | 'list' | null {
  const params = new URLSearchParams(window.location.search);
  const v = params.get('view');
  if (v === 'list') return 'list';
  if (v === 'board') return 'board';
  return null;
}

export function App() {
  const setUser = useAuthStore((s) => s.setUser);
  const user = useAuthStore((s) => s.user);
  const { activeView, setView, createTicketOpen, setCreateTicketOpen, ticketDetailId, closeTicketDetail, openTicketDetail } = useUIStore();

  // Init theme from localStorage
  useEffect(() => {
    useThemeStore.getState().initTheme();
  }, []);

  // Init from server config
  useEffect(() => {
    const config = window.__APP_CONFIG__;
    if (config?.user) setUser(config.user);
    if (config?.mode) {
      // Map legacy 'dashboard' mode to 'tickets'
      const mode = config.mode === 'dashboard' ? 'tickets' : config.mode;
      setView(mode as View);
    }

    // Deep link: /tickets/<id> opens ticket detail
    if (config?.ticket_id) {
      openTicketDetail(config.ticket_id);
    } else {
      const ticketId = getTicketIdFromPath(window.location.pathname);
      if (ticketId) openTicketDetail(ticketId);
    }

    // Restore view mode from URL
    const urlViewMode = getViewModeFromURL();
    if (urlViewMode) {
      useTicketStore.getState().setViewMode(urlViewMode);
    }
  }, []);

  // Redirect end_user to portal if they land on a non-portal view
  useEffect(() => {
    if (user?.role === 'end_user' && activeView !== 'portal') {
      setView('portal');
      replaceUrl('/portal');
    }
  }, [user, activeView]);

  // Browser back/forward
  useEffect(() => {
    const onPopState = () => {
      const path = window.location.pathname;
      const view = pathToView(path);
      setView(view);

      const ticketId = getTicketIdFromPath(path);
      if (ticketId) {
        useUIStore.setState({ ticketDetailId: ticketId });
      } else {
        useUIStore.setState({ ticketDetailId: null });
      }

      const urlViewMode = getViewModeFromURL();
      if (urlViewMode) {
        useTicketStore.getState().setViewMode(urlViewMode);
      }
    };
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  // Escape key closes modals/panels
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (ticketDetailId) { closeTicketDetail(); return; }
        if (createTicketOpen) { setCreateTicketOpen(false); return; }
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [ticketDetailId, createTicketOpen]);

  const renderView = () => {
    switch (activeView) {
      case 'kb':
        return <KnowledgeBase />;
      case 'chat':
        return window.__APP_CONFIG__?.ai_chat_enabled
          ? <ChatPanel />
          : <div className="empty-state"><div className="empty-state-title">AI Chat not enabled</div><div className="empty-state-text">Contact your admin to enable the AI Chat module.</div></div>;
      case 'admin':
        return <AdminPanel />;
      case 'audit':
        return <AuditQueue />;
      case 'reports':
        return <Reports />;
      case 'sprints':
        return <SprintManager />;
      case 'automations': {
        const stripped = stripSlug(window.location.pathname);
        const automationIdMatch = stripped.match(/^\/automations\/(\d+)$/);
        if (automationIdMatch) {
          return <AutomationBuilder automationId={parseInt(automationIdMatch[1], 10)} />;
        }
        return <AutomationList />;
      }
      case 'portal':
        return <CustomerPortal />;
      case 'tickets':
      default:
        return <TicketBoard />;
    }
  };

  const config = window.__APP_CONFIG__;
  const showDemoBanner =
    config?.demo_mode === true && user?.tenant_id != null;

  // Portal mode: simplified layout (no sidebar for end_users)
  if (user?.role === 'end_user') {
    return (
      <div className="app-shell">
        <div className="app-main app-main-full">
          <Topbar />
          <div className="app-content view-fade-in">
            {renderView()}
          </div>
        </div>
        {user && <IdleTimeoutModal />}
      </div>
    );
  }

  return (
    <div className="app-shell">
      <HexGrid />
      <Sidebar />
      <div className="app-main">
        <Topbar />
        {showDemoBanner && (
          <DemoBanner
            trialExpiresAt={config.trial_expires_at}
            byokConfigured={config.byok_configured ?? false}
          />
        )}
        <div className="app-content view-fade-in">
          {renderView()}
        </div>
      </div>

      {/* Global overlays */}
      <CreateTicketModal />
      <TourOverlay />
      {user && <IdleTimeoutModal />}
    </div>
  );
}
