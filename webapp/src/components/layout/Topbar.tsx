import { useEffect, useState, useRef, useCallback } from 'react';
import { useUIStore } from '../../store/uiStore';
import { useAuthStore } from '../../store/authStore';
import { useThemeStore } from '../../store/themeStore';
import { api } from '../../api/client';
import { UserSettings } from '../common/UserSettings';

const VIEW_TITLES: Record<string, string> = {
  tickets: 'Tickets',
  kb: 'Knowledge Base',
  chat: 'Atlas',
  admin: 'Administration',
  portal: 'My Cases',
};

const ACCENT_COLORS = [
  { key: 'green' as const, color: '#44dd44' },
  { key: 'red' as const, color: '#ff4444' },
  { key: 'gold' as const, color: '#ddaa22' },
  { key: 'blue' as const, color: '#4488ff' },
  { key: 'white' as const, color: '#ffffff' },
];

interface InAppNotification {
  id: number;
  ticket_id: number;
  event: string;
  ticket_number: string;
  subject: string;
  status: string;
  priority: string;
  created_at: string;
}

const EVENT_LABELS: Record<string, string> = {
  ticket_created: 'New Ticket',
  ticket_assigned: 'Assigned',
  ticket_resolved: 'Resolved',
  ticket_closed: 'Closed',
  status_changed: 'Status Changed',
  priority_changed: 'Priority Changed',
  agent_reply: 'New Reply',
  requester_reply: 'Requester Reply',
  sla_warning: 'SLA Warning',
  sla_breach: 'SLA Breach',
  team_assigned: 'Team Assigned',
};

export function Topbar() {
  const activeView = useUIStore((s) => s.activeView);
  const user = useAuthStore((s) => s.user);
  const setCreateTicketOpen = useUIStore((s) => s.setCreateTicketOpen);
  const openTicketDetail = useUIStore((s) => s.openTicketDetail);
  const accent = useThemeStore((s) => s.accent);
  const mode = useThemeStore((s) => s.mode);
  const setAccent = useThemeStore((s) => s.setAccent);
  const setMode = useThemeStore((s) => s.setMode);

  const [notifications, setNotifications] = useState<InAppNotification[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [bellOpen, setBellOpen] = useState(false);
  const bellRef = useRef<HTMLDivElement>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const isEndUser = user?.role === 'end_user';

  const fetchNotifications = useCallback(async () => {
    if (isEndUser) return;
    try {
      const data = await api.getUnreadNotifications();
      setNotifications(data.notifications || []);
      setUnreadCount(data.count || 0);
    } catch {
      // Silently fail — bell just shows no count
    }
  }, [isEndUser]);

  // Poll every 30 seconds
  useEffect(() => {
    fetchNotifications();
    const interval = setInterval(fetchNotifications, 30000);
    return () => clearInterval(interval);
  }, [fetchNotifications]);

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (bellRef.current && !bellRef.current.contains(e.target as Node)) {
        setBellOpen(false);
      }
    };
    if (bellOpen) document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [bellOpen]);

  const handleMarkAllRead = async () => {
    try {
      await api.markNotificationsRead();
      setNotifications([]);
      setUnreadCount(0);
    } catch {
      // ignore
    }
  };

  const handleNotificationClick = (n: InAppNotification) => {
    if (n.ticket_id) {
      openTicketDetail(n.ticket_id);
    }
    setBellOpen(false);
  };

  const formatTime = (dateStr: string) => {
    if (!dateStr) return '';
    const d = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return 'just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHrs = Math.floor(diffMin / 60);
    if (diffHrs < 24) return `${diffHrs}h ago`;
    const diffDays = Math.floor(diffHrs / 24);
    return `${diffDays}d ago`;
  };

  return (
    <div className="topbar">
      <div className="topbar-title">{VIEW_TITLES[activeView] || 'Helpdesk'}</div>
      <div className="topbar-actions">
        {activeView === 'tickets' && (
          <button className="btn btn-primary" onClick={() => setCreateTicketOpen(true)}>
            + New Ticket
          </button>
        )}

        {/* Notification Bell */}
        {!isEndUser && (
          <div className="notification-bell-wrap" ref={bellRef}>
            <button
              className="notification-bell-btn"
              onClick={() => setBellOpen(!bellOpen)}
              title={`${unreadCount} unread notification${unreadCount !== 1 ? 's' : ''}`}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M18 8A6 6 0 006 8c0 7-3 9-3 9h18s-3-2-3-9" />
                <path d="M13.73 21a2 2 0 01-3.46 0" />
              </svg>
              {unreadCount > 0 && (
                <span className="notification-bell-badge">{unreadCount > 99 ? '99+' : unreadCount}</span>
              )}
            </button>
            {bellOpen && (
              <div className="notification-bell-dropdown">
                <div className="notification-bell-header">
                  <span className="notification-bell-title">Notifications</span>
                  {unreadCount > 0 && (
                    <button className="btn btn-ghost btn-xs" onClick={handleMarkAllRead}>
                      Mark all read
                    </button>
                  )}
                </div>
                <div className="notification-bell-list">
                  {notifications.length === 0 ? (
                    <div className="notification-bell-empty">No new notifications</div>
                  ) : (
                    notifications.map((n) => (
                      <button
                        key={n.id}
                        className="notification-bell-item"
                        onClick={() => handleNotificationClick(n)}
                      >
                        <span className="notification-bell-event">{EVENT_LABELS[n.event] || n.event}</span>
                        <span className="notification-bell-subject">
                          {n.ticket_number ? `${n.ticket_number}: ` : ''}{n.subject}
                        </span>
                        <span className="notification-bell-time">{formatTime(n.created_at)}</span>
                      </button>
                    ))
                  )}
                </div>
              </div>
            )}
          </div>
        )}

        <div className="theme-picker">
          {ACCENT_COLORS.map((c) => (
            <button
              key={c.key}
              className={`theme-dot${accent === c.key ? ' active' : ''}`}
              style={{ background: c.color }}
              onClick={() => setAccent(c.key)}
              title={c.key}
            />
          ))}
          <button
            className="theme-mode-btn"
            onClick={() => setMode(mode === 'dark' ? 'light' : 'dark')}
            title={mode === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
          >
            {mode === 'dark' ? '\u2600' : '\u263E'}
          </button>
        </div>

        <button
          className="topbar-user-btn"
          onClick={() => setSettingsOpen(true)}
          title="Account Settings"
          aria-label="Open account settings"
        >
          {user?.name}{user?.email ? ` (${user.email})` : ''}
        </button>
        <a href="/logout" className="btn btn-ghost btn-sm">Logout</a>
      </div>
      {settingsOpen && <UserSettings onClose={() => setSettingsOpen(false)} />}
    </div>
  );
}
