import { useUIStore, type View } from '../../store/uiStore';
import { useAuthStore } from '../../store/authStore';
import { pushUrl } from '../../utils/url';

const IconDashboard = () => (
  <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <rect x="1.5" y="1.5" width="6" height="6" rx="1" />
    <rect x="10.5" y="1.5" width="6" height="6" rx="1" />
    <rect x="1.5" y="10.5" width="6" height="6" rx="1" />
    <rect x="10.5" y="10.5" width="6" height="6" rx="1" />
  </svg>
);

const IconTickets = () => (
  <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <line x1="4" y1="4" x2="14" y2="4" />
    <line x1="4" y1="7.5" x2="14" y2="7.5" />
    <line x1="4" y1="11" x2="14" y2="11" />
    <line x1="4" y1="14.5" x2="10" y2="14.5" />
  </svg>
);

const IconKB = () => (
  <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M2 3.5C2 2.67 2.67 2 3.5 2H7c.55 0 1.08.22 1.47.59L9 3l.53-.41C9.92 2.22 10.45 2 11 2h3.5c.83 0 1.5.67 1.5 1.5V14c0 .83-.67 1.5-1.5 1.5H11c-.55 0-1.08.22-1.47.59L9 16.5l-.53-.41C8.08 15.72 7.55 15.5 7 15.5H3.5C2.67 15.5 2 14.83 2 14V3.5z" />
    <line x1="9" y1="3" x2="9" y2="16.5" />
  </svg>
);

const IconChat = () => (
  <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 3h12c.83 0 1.5.67 1.5 1.5v7c0 .83-.67 1.5-1.5 1.5H6l-3.5 2.5V4.5C2.5 3.67 3.17 3 4 3z" />
    <circle cx="6.5" cy="8" r="0.5" fill="currentColor" stroke="none" />
    <circle cx="9" cy="8" r="0.5" fill="currentColor" stroke="none" />
    <circle cx="11.5" cy="8" r="0.5" fill="currentColor" stroke="none" />
  </svg>
);

interface NavItem {
  view: View;
  icon: () => React.ReactNode;
  label: string;
}

const BASE_NAV_ITEMS: NavItem[] = [
  { view: 'tickets', icon: IconTickets, label: 'Tickets' },
  { view: 'kb', icon: IconKB, label: 'Knowledge Base' },
];

const CHAT_NAV_ITEM: NavItem = { view: 'chat', icon: IconChat, label: 'Atlas' };

const IconAudit = () => (
  <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M9 1.5L2 5v4c0 4.42 2.99 8.06 7 9 4.01-.94 7-4.58 7-9V5L9 1.5z" />
    <path d="M6 9l2 2 4-4" />
  </svg>
);

const AUDIT_NAV_ITEM: NavItem = { view: 'audit', icon: IconAudit, label: 'AI Audit' };

const ADMIN_ITEMS: NavItem[] = [
  { view: 'admin', icon: () => <span style={{ fontSize: 16 }}>{'\u2699'}</span>, label: 'Admin' },
];

export function Sidebar() {
  const { activeView, setView } = useUIStore();
  const isAdmin = useAuthStore((s) => s.isAdmin);
  const hasPermission = useAuthStore((s) => s.hasPermission);
  const hasAnyPermission = useAuthStore((s) => s.hasAnyPermission);
  const cfg = window.__APP_CONFIG__;
  const aiChatEnabled = cfg?.ai_chat_enabled;
  const userPerms: string[] = cfg?.user?.permissions || [];
  const showAtlasNav = aiChatEnabled && userPerms.includes('atlas.chat');

  const logoUrl = cfg?.tenant_logo_url || cfg?.tenant_settings?.portal_logo_url || '';
  const brandName = cfg?.tenant_settings?.app_name || cfg?.app_name || 'Helpdesk';

  const navItems = showAtlasNav
    ? [...BASE_NAV_ITEMS, CHAT_NAV_ITEM]
    : BASE_NAV_ITEMS;

  return (
    <nav className="sidebar">
      <div className="sidebar-brand">
        {logoUrl ? (
          <img src={logoUrl} alt="" className="sidebar-brand-logo" />
        ) : (
          <div className="sidebar-brand-icon">{brandName.charAt(0).toUpperCase()}</div>
        )}
        <span className="sidebar-brand-text">{brandName}</span>
      </div>
      <div className="sidebar-nav">
        <div className="nav-section">Main</div>
        {/* Tickets */}
        {navItems.filter(i => i.view === 'tickets').map((item) => (
          <button key={item.view} data-tour={`nav-${item.view}`}
            className={`nav-item ${activeView === item.view ? 'active' : ''}`}
            onClick={() => { setView(item.view); pushUrl(`/${item.view}`); }}>
            <span className="nav-icon"><item.icon /></span>
            <span className="nav-label">{item.label}</span>
          </button>
        ))}
        {/* Sprints */}
        <button className={`nav-item ${activeView === 'sprints' ? 'active' : ''}`}
          onClick={() => { setView('sprints'); pushUrl('/sprints'); }}>
          <span className="nav-icon">
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 3h12v4H3zM3 11h12v4H3z" /><path d="M7 7v4M11 7v4" />
            </svg>
          </span>
          <span className="nav-label">Sprints</span>
        </button>
        {/* Atlas */}
        {navItems.filter(i => i.view === 'chat').map((item) => (
          <button key={item.view} data-tour={`nav-${item.view}`}
            className={`nav-item ${activeView === item.view ? 'active' : ''}`}
            onClick={() => { setView(item.view); pushUrl(`/${item.view}`); }}>
            <span className="nav-icon"><item.icon /></span>
            <span className="nav-label">{item.label}</span>
          </button>
        ))}
        {/* Knowledge Base */}
        {navItems.filter(i => i.view === 'kb').map((item) => (
          <button key={item.view} data-tour={`nav-${item.view}`}
            className={`nav-item ${activeView === item.view ? 'active' : ''}`}
            onClick={() => { setView(item.view); pushUrl(`/${item.view}`); }}>
            <span className="nav-icon"><item.icon /></span>
            <span className="nav-label">{item.label}</span>
          </button>
        ))}
        {/* AI Audit */}
        {hasPermission('audit.view') && (
          <button className={`nav-item ${activeView === 'audit' ? 'active' : ''}`}
            onClick={() => { setView('audit'); pushUrl('/audit'); }}>
            <span className="nav-icon"><AUDIT_NAV_ITEM.icon /></span>
            <span className="nav-label">{AUDIT_NAV_ITEM.label}</span>
          </button>
        )}
        {/* Reports */}
        {hasPermission('reports.view') && (
          <button className={`nav-item ${activeView === 'reports' ? 'active' : ''}`}
            onClick={() => { setView('reports'); pushUrl('/reports'); }}>
            <span className="nav-icon">
              <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <rect x="2" y="10" width="3" height="6" rx="0.5" /><rect x="7.5" y="6" width="3" height="10" rx="0.5" /><rect x="13" y="2" width="3" height="14" rx="0.5" />
              </svg>
            </span>
            <span className="nav-label">Reports</span>
          </button>
        )}
        {/* Automations */}
        {hasPermission('automations.manage') && (
          <button className={`nav-item ${activeView === 'automations' ? 'active' : ''}`}
            onClick={() => { setView('automations'); pushUrl('/automations'); }}>
            <span className="nav-icon">
              <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="4" cy="4" r="2" /><circle cx="14" cy="4" r="2" /><circle cx="9" cy="14" r="2" />
                <path d="M6 4h6M4 6v4l5 4M14 6v4l-5 4" />
              </svg>
            </span>
            <span className="nav-label">Automations</span>
          </button>
        )}

        {hasAnyPermission('users.manage', 'categories.manage', 'locations.manage', 'atlas.admin') && (
          <>
            <div className="nav-section">Administration</div>
            {ADMIN_ITEMS.map((item) => (
              <button
                key={item.view}
                data-tour={`nav-${item.view}`}
                className={`nav-item ${activeView === item.view ? 'active' : ''}`}
                onClick={() => {
                  setView(item.view);
                  pushUrl(`/${item.view}`);
                }}
              >
                <span className="nav-icon"><item.icon /></span>
                <span className="nav-label">{item.label}</span>
              </button>
            ))}
          </>
        )}
      </div>
    </nav>
  );
}
