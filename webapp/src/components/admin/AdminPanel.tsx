import { useEffect, useState, useRef } from 'react';
import { api } from '../../api/client';
import { useAuthStore } from '../../store/authStore';
import { pushUrl, stripSlug } from '../../utils/url';
import { useHierarchyStore } from '../../store/hierarchyStore';
import { NotificationManager } from './NotificationManager';
import { LocationImport } from './LocationImport';
import { LocationDbSync } from './LocationDbSync';
import { CategoryDbSync } from './CategoryDbSync';
import { TierView } from './TierView';
import { PipelineMonitor } from './PipelineMonitor';
import { SystemErrors } from './SystemErrors';
import { UsagePanel } from './UsagePanel';
import { BillingPanel } from './BillingPanel';
import { PhoneSettings } from './PhoneSettings';
import { StatusPageAdmin } from './StatusPageAdmin';
import { FormDesigner } from './FormDesigner';
import { AdminBanner, resetAllBanners } from './AdminBanner';
import { PortalHero } from '../portal/PortalHero';
import { PortalCardGrid } from '../portal/PortalCardGrid';
import type { Tenant, KnowledgeModule, AdminUser, PortalCard, ModuleFeature } from '../../types';
import { DEFAULT_PORTAL_CARDS, BACKGROUND_PRESETS } from '../../types';

type Tab = 'tenants' | 'users' | 'groups' | 'teams' | 'locations' | 'categories' | 'custom-fields' | 'notifications' | 'portal' | 'status-page' | 'pipeline' | 'usage' | 'system_log' | 'billing' | 'phone' | 'reports' | 'branding';

const ICON_OPTIONS = [
  'alert-circle', 'search', 'book', 'message-circle', 'file-text',
  'users', 'help-circle', 'settings', 'phone', 'mail', 'zap', 'shield', 'activity',
];

const ACTION_OPTIONS: { value: PortalCard['action']; label: string }[] = [
  { value: 'create_ticket', label: 'Create Ticket' },
  { value: 'my_tickets', label: 'My Tickets' },
  { value: 'kb', label: 'Knowledge Base' },
  { value: 'chat', label: 'AI Chat' },
  { value: 'status', label: 'System Status' },
  { value: 'url', label: 'External URL' },
];

// Legacy URL slugs → flat tab keys
const LEGACY_TAB_MAP: Record<string, Tab> = {
  team: 'users', system: 'pipeline',
};

const ALL_TABS: Tab[] = ['tenants', 'users', 'groups', 'teams', 'locations', 'categories', 'custom-fields', 'notifications', 'portal', 'pipeline', 'usage', 'system_log', 'billing', 'phone', 'reports', 'branding'];

export function AdminPanel() {
  const isSuperAdmin = useAuthStore((s) => s.isSuperAdmin);
  const hasPermission = useAuthStore((s) => s.hasPermission);

  const parseTabFromURL = (): Tab | null => {
    const stripped = stripSlug(window.location.pathname);
    const match = stripped.match(/^\/admin\/([\w-]+)/);
    if (match) {
      const s = match[1];
      if (ALL_TABS.includes(s as Tab)) return s as Tab;
      if (LEGACY_TAB_MAP[s]) return LEGACY_TAB_MAP[s];
    }
    return null;
  };

  const [bannerGen, setBannerGen] = useState(0);

  const [tab, setTab] = useState<Tab>(() => {
    return parseTabFromURL() ?? (isSuperAdmin() ? 'tenants' : 'users');
  });

  // Sync tab on browser back/forward
  useEffect(() => {
    const onPopState = () => {
      const t = parseTabFromURL();
      if (t && t !== tab) setTab(t);
    };
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, [tab]);

  const handleTabChange = (t: Tab) => {
    setTab(t);
    pushUrl(`/admin/${t}`);
  };

  const platformItems: { key: Tab; label: string }[] = [
    { key: 'tenants',    label: 'Tenants' },
    { key: 'pipeline',   label: 'Pipeline' },
    { key: 'system_log', label: 'System Log' },
    { key: 'usage',      label: 'Token Usage' },
  ];

  const workspaceItems: { key: Tab; label: string; permission?: string }[] = [
    { key: 'users',         label: 'Users',         permission: 'users.manage' },
    { key: 'groups',        label: 'Access Control', permission: 'users.manage' },
    { key: 'teams',         label: 'Teams',         permission: 'users.manage' },
    { key: 'locations',     label: 'Locations',     permission: 'locations.manage' },
    { key: 'categories',    label: 'Categories',    permission: 'categories.manage' },
    { key: 'custom-fields', label: 'Form Designer',  permission: 'categories.manage' },
    { key: 'branding',      label: 'Branding' },
    { key: 'notifications', label: 'Notifications' },
    { key: 'portal',        label: 'Portal' },
    { key: 'status-page',   label: 'Status Page' },
    { key: 'phone',         label: 'Communications' },
    { key: 'billing',       label: 'Billing' },
  ];

  const visibleWorkspace = workspaceItems.filter(
    (item) => !item.permission || hasPermission(item.permission)
  );

  return (
    <div className="admin-layout">
      <nav className="admin-sidenav">
        {isSuperAdmin() && (
          <div className="admin-sidenav-group">
            <div className="admin-sidenav-heading">Platform</div>
            {platformItems.map((item) => (
              <button
                key={item.key}
                className={`admin-sidenav-item${tab === item.key ? ' active' : ''}`}
                onClick={() => handleTabChange(item.key)}
              >
                {item.label}
              </button>
            ))}
          </div>
        )}
        <div className="admin-sidenav-group">
          {isSuperAdmin() && <div className="admin-sidenav-heading">Workspace</div>}
          {visibleWorkspace.map((item) => (
            <button
              key={item.key}
              className={`admin-sidenav-item${tab === item.key ? ' active' : ''}`}
              onClick={() => handleTabChange(item.key)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <button
          className="admin-sidenav-item"
          style={{ marginTop: 'auto', fontSize: 11, color: 'var(--t-text-muted)', opacity: 0.7 }}
          onClick={() => { resetAllBanners(); setBannerGen((g) => g + 1); }}
        >
          Show help tips
        </button>
      </nav>

      <div className="admin-content">
        {tab === 'tenants'       && isSuperAdmin() && <TenantList onNavigateTab={handleTabChange} />}
        {tab === 'pipeline'      && isSuperAdmin() && <PipelineMonitor />}
        {tab === 'system_log'    && isSuperAdmin() && <SystemErrors />}
        {tab === 'usage'         && <UsagePanel />}
        {tab === 'billing'       && <BillingPanel />}
        {tab === 'users' && <>
          <AdminBanner generation={bannerGen} id="users" title="User Management">
            Manage your team members and end users. Invite agents, assign roles, and control access.
            Users can be assigned to <strong>groups</strong> for permission bundles, <strong>teams</strong> for ticket routing,
            and <strong>locations</strong> for site-specific access. Atlas uses user data to suggest the best agent for each ticket.
          </AdminBanner>
          <UserList />
        </>}
        {tab === 'groups' && <>
          <AdminBanner generation={bannerGen} id="groups" title="Access Control Groups">
            Groups bundle permissions together so you don't have to manage them per-user.
            Create groups like "Managers" or "Read-Only Agents" and assign permissions once — every member inherits them.
            The <strong>Workflow Automator</strong> can route tickets to groups, and <strong>notifications</strong> can be configured per-group.
          </AdminBanner>
          <GroupManager />
        </>}
        {tab === 'teams' && <>
          <AdminBanner generation={bannerGen} id="teams" title="Teams">
            Teams represent functional groups that work together — like "Network Support" or "POS Team."
            Assign agents to teams, then use the <strong>Workflow Automator</strong> to auto-route tickets to the right team based on
            category, priority, or keywords. Atlas considers team assignments when suggesting the best agent for a ticket.
            Teams also appear in ticket filters and reports for workload analysis.
          </AdminBanner>
          <TeamManager />
        </>}
        {tab === 'locations' && <>
          <AdminBanner generation={bannerGen} id="locations" title="Location Hierarchy">
            Locations represent your physical sites — stores, offices, warehouses — organized in a hierarchy (e.g. Region &rarr; City &rarr; Store).
            When users submit tickets, their location helps Atlas route to the right team and provides context for troubleshooting.
            You can <strong>import</strong> locations from a spreadsheet or <strong>DB Sync</strong> to keep them updated from an external database automatically.
          </AdminBanner>
          <LocationManager />
        </>}
        {tab === 'categories' && <>
          <AdminBanner generation={bannerGen} id="categories" title="Problem Categories">
            Categories define the types of issues your users can report — organized in a hierarchy (e.g. "Hardware" &rarr; "Printer" &rarr; "Paper Jam").
            Each category can have a <strong>default priority</strong> so urgent issue types are automatically escalated.
            Atlas uses categories to find relevant knowledge base articles and route tickets to specialized agents.
            Import categories from a spreadsheet or use <strong>DB Sync</strong> to keep them in sync with an external system.
          </AdminBanner>
          <ProblemCategoryManager />
        </>}
        {tab === 'custom-fields' && <FormDesigner />}
        {tab === 'branding' && <>
          <AdminBanner generation={bannerGen} id="branding" title="Branding">
            Customize your helpdesk's appearance — set your company name, logo, and accent colors.
            These settings apply across the agent interface and the customer portal, keeping your brand consistent.
          </AdminBanner>
          <BrandingSettings />
        </>}
        {tab === 'notifications' && <>
          <AdminBanner generation={bannerGen} id="notifications" title="Notification Rules">
            Control who gets notified and when. Configure per-event rules for ticket creation, assignment, replies, SLA breaches, and more.
            Notifications can target <strong>ticket participants</strong> (requester, assignee), <strong>groups</strong>, or <strong>teams</strong>.
            Each channel (in-app, email, Teams/Slack) can be toggled independently. Fine-tune these to reduce noise and ensure the right people are always informed.
          </AdminBanner>
          <NotificationManager />
        </>}
        {tab === 'portal' && <>
          <AdminBanner generation={bannerGen} id="portal" title="Customer Portal">
            The portal is what your end users see — a self-service interface where they submit tickets, track status, browse the knowledge base, and chat with Atlas.
            Customize the greeting, hero background, action cards, and branding. The portal automatically inherits your theme and displays your logo.
          </AdminBanner>
          <PortalSettingsWithPreview />
        </>}
        {tab === 'status-page' && <>
          <AdminBanner generation={bannerGen} id="status-page" title="Status Page">
            Communicate planned maintenance and outages to your users. Create incidents with severity levels, post timeline updates,
            and resolve them when fixed. The status page is visible from the customer portal under "System Status."
            When active incidents exist, Atlas can inform users submitting related tickets that an outage may be the cause.
          </AdminBanner>
          <StatusPageAdmin />
        </>}
        {tab === 'phone' && <>
          <AdminBanner generation={bannerGen} id="phone" title="Communications">
            Configure voice AI agents (powered by ElevenLabs + Twilio), SMS, and WhatsApp messaging.
            Voice agents answer calls, search your knowledge base, identify callers, and create tickets automatically.
            SMS and WhatsApp let you reach users on their preferred channel with auto-replies and ticket creation.
          </AdminBanner>
          <PhoneSettings />
        </>}
        {tab === 'reports'       && <ReportsPlaceholder />}
      </div>
    </div>
  );
}


/* ============================================================
   PERMISSION MATRIX (RBAC) — replaces old GroupManager
   ============================================================ */
function GroupManager() {
  const tenantId = useAuthStore((s) => s.user?.tenant_id);
  const isSuperAdmin = useAuthStore((s) => s.isSuperAdmin);

  // Data state
  const [groups, setGroups] = useState<any[]>([]);
  const [permissions, setPermissions] = useState<any[]>([]);
  const [matrix, setMatrix] = useState<Record<string, number[]>>({});
  const [originalMatrix, setOriginalMatrix] = useState<Record<string, number[]>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  // Group create
  const [newGroupName, setNewGroupName] = useState('');

  // Members panel
  const [expandedGroupId, setExpandedGroupId] = useState<number | null>(null);
  const [members, setMembers] = useState<any[]>([]);
  const [allUsers, setAllUsers] = useState<any[]>([]);
  const [membersLoading, setMembersLoading] = useState(false);

  // Copy-from dropdown
  const [copyFromOpen, setCopyFromOpen] = useState<number | null>(null);

  // Default permission templates (by group name) for reset-to-default
  const DEFAULT_GROUP_SLUGS: Record<string, string[] | 'all'> = {
    'Agents': ['tickets.view', 'tickets.create', 'atlas.chat'],
    'Senior Agents': ['tickets.view', 'tickets.create', 'atlas.chat', 'tickets.close', 'tickets.assign', 'audit.view', 'sprints.manage'],
    'Managers': [
      'tickets.view', 'tickets.create', 'atlas.chat',
      'tickets.close', 'tickets.assign', 'audit.view',
      'metrics.view', 'audit.review', 'audit.kba',
      'categories.manage', 'locations.manage',
      'reports.view', 'automations.manage',
      'teams.manage', 'kb.manage', 'sprints.manage', 'notifications.manage',
    ],
    'Admins': 'all',
  };

  const loadMatrix = async () => {
    try {
      const data = await api.getPermissionMatrix(isSuperAdmin() ? tenantId ?? undefined : undefined);
      setGroups(data.groups);
      setPermissions(data.permissions);
      setMatrix(structuredClone(data.matrix));
      setOriginalMatrix(structuredClone(data.matrix));
    } catch {}
  };

  useEffect(() => {
    Promise.all([
      loadMatrix(),
      api.listUsers().then(r => setAllUsers(r)),
    ]).finally(() => setLoading(false));
  }, []);

  // Dirty tracking
  const dirtyGroupIds = (): number[] => {
    const dirty: number[] = [];
    for (const g of groups) {
      const gid = String(g.id);
      const curr = (matrix[gid] || []).slice().sort((a: number, b: number) => a - b);
      const orig = (originalMatrix[gid] || []).slice().sort((a: number, b: number) => a - b);
      if (curr.length !== orig.length || curr.some((v: number, i: number) => v !== orig[i])) {
        dirty.push(g.id);
      }
    }
    return dirty;
  };

  const isDirty = dirtyGroupIds().length > 0;

  const isCellDirty = (groupId: number, permId: number): boolean => {
    const gid = String(groupId);
    const currHas = (matrix[gid] || []).includes(permId);
    const origHas = (originalMatrix[gid] || []).includes(permId);
    return currHas !== origHas;
  };

  // Permission grouping by category
  const permsByCategory: [string, any[]][] = [];
  const catMap: Record<string, any[]> = {};
  for (const p of permissions) {
    if (!catMap[p.category]) {
      catMap[p.category] = [];
      permsByCategory.push([p.category, catMap[p.category]]);
    }
    catMap[p.category].push(p);
  }

  // Toggle single cell
  const toggleCell = (groupId: number, permId: number) => {
    setMatrix(prev => {
      const gid = String(groupId);
      const arr = prev[gid] || [];
      return {
        ...prev,
        [gid]: arr.includes(permId) ? arr.filter((id: number) => id !== permId) : [...arr, permId],
      };
    });
  };

  // Row-level toggle: if all groups have this perm, uncheck all; otherwise check all
  const toggleRow = (permId: number) => {
    const allHave = groups.every(g => (matrix[String(g.id)] || []).includes(permId));
    setMatrix(prev => {
      const next = { ...prev };
      for (const g of groups) {
        const gid = String(g.id);
        const arr = next[gid] || [];
        if (allHave) {
          next[gid] = arr.filter((id: number) => id !== permId);
        } else if (!arr.includes(permId)) {
          next[gid] = [...arr, permId];
        }
      }
      return next;
    });
  };

  // Column-level select all / clear all
  const selectAllForGroup = (groupId: number) => {
    setMatrix(prev => ({
      ...prev,
      [String(groupId)]: permissions.map((p: any) => p.id),
    }));
  };

  const clearAllForGroup = (groupId: number) => {
    setMatrix(prev => ({
      ...prev,
      [String(groupId)]: [],
    }));
  };

  // Copy from another group
  const copyFrom = (targetGroupId: number, sourceGroupId: number) => {
    setMatrix(prev => ({
      ...prev,
      [String(targetGroupId)]: [...(prev[String(sourceGroupId)] || [])],
    }));
    setCopyFromOpen(null);
  };

  // Reset group to default permissions
  const resetToDefault = (group: any) => {
    const template = DEFAULT_GROUP_SLUGS[group.name];
    if (!template) return;
    const ids = template === 'all'
      ? permissions.map((p: any) => p.id)
      : permissions.filter((p: any) => template.includes(p.slug)).map((p: any) => p.id);
    setMatrix(prev => ({ ...prev, [String(group.id)]: ids }));
  };

  const hasDefault = (groupName: string) => groupName in DEFAULT_GROUP_SLUGS;

  // Save
  const handleSave = async () => {
    const dirty = dirtyGroupIds();
    if (dirty.length === 0) return;
    setSaving(true);
    try {
      const payload: Record<number, number[]> = {};
      for (const gid of dirty) {
        payload[gid] = matrix[String(gid)] || [];
      }
      await api.savePermissionMatrix(payload);
      setOriginalMatrix(structuredClone(matrix));
    } catch {}
    setSaving(false);
  };

  // Discard
  const handleDiscard = () => {
    setMatrix(structuredClone(originalMatrix));
  };

  // Group CRUD
  const createGroup = async () => {
    if (!newGroupName.trim()) return;
    setSaving(true);
    try {
      await api.createGroup({ name: newGroupName.trim(), tenant_id: tenantId ?? undefined });
      setNewGroupName('');
      await loadMatrix();
    } catch {}
    setSaving(false);
  };

  const deleteGroup = async (id: number) => {
    if (!confirm('Delete this group? Members will be moved to the default group.')) return;
    try {
      await api.deleteGroup(id);
      if (expandedGroupId === id) setExpandedGroupId(null);
      await loadMatrix();
    } catch {}
  };

  // Members panel
  const toggleMembersPanel = async (groupId: number) => {
    if (expandedGroupId === groupId) {
      setExpandedGroupId(null);
      return;
    }
    setExpandedGroupId(groupId);
    setMembersLoading(true);
    try {
      const data = await api.getGroupMembers(groupId);
      setMembers(data);
    } catch {}
    setMembersLoading(false);
  };

  const addMember = async (userId: number) => {
    if (!expandedGroupId) return;
    const currentIds = members.map((m: any) => m.id);
    if (currentIds.includes(userId)) return;
    await api.setGroupMembers(expandedGroupId, [...currentIds, userId]);
    const data = await api.getGroupMembers(expandedGroupId);
    setMembers(data);
    await loadMatrix();
  };

  const removeMember = async (userId: number) => {
    if (!expandedGroupId) return;
    const newIds = members.filter((m: any) => m.id !== userId).map((m: any) => m.id);
    await api.setGroupMembers(expandedGroupId, newIds);
    const data = await api.getGroupMembers(expandedGroupId);
    setMembers(data);
    await loadMatrix();
  };

  if (loading) return <div className="loading-text">Loading access control...</div>;

  const borderStyle = '1px solid var(--t-border-subtle, rgba(255,255,255,0.06))';
  const expandedGroup = expandedGroupId ? groups.find((g: any) => g.id === expandedGroupId) : null;

  return (
    <div>
      {/* Toolbar: Create group + Save/Discard */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16, flexWrap: 'wrap', gap: 12 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <input
            className="input"
            placeholder="New group name..."
            value={newGroupName}
            onChange={(e) => setNewGroupName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && createGroup()}
            style={{ width: 200 }}
            aria-label="New group name"
          />
          <button className="btn btn-primary btn-sm" onClick={createGroup} disabled={saving || !newGroupName.trim()} aria-label="Create group">
            + Create Group
          </button>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {isDirty && (
            <button className="btn btn-ghost btn-sm" onClick={handleDiscard} aria-label="Discard changes">
              Discard
            </button>
          )}
          <button
            className="btn btn-primary"
            onClick={handleSave}
            disabled={saving || !isDirty}
            style={{ fontWeight: 600 }}
            aria-label="Save permission changes"
          >
            {saving ? 'Saving...' : isDirty ? `Save Changes (${dirtyGroupIds().length})` : 'Save Changes'}
          </button>
        </div>
      </div>

      {/* Matrix table */}
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <div style={{ overflowX: 'auto', overflowY: 'visible', scrollbarWidth: 'thin', scrollbarColor: 'var(--t-text-muted) transparent' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, minWidth: groups.length * 160 + 300 }}>
            <thead>
              {/* Group name headers */}
              <tr>
                <th style={{
                  position: 'sticky', left: 0, zIndex: 3,
                  textAlign: 'left', padding: '10px 14px', fontWeight: 600,
                  color: 'var(--t-text-dim)', borderBottom: borderStyle,
                  background: 'var(--t-panel, var(--t-bg))',
                  minWidth: 300,
                }}>
                  Permission
                </th>
                {groups.map((g: any) => (
                  <th key={g.id} style={{
                    position: 'sticky', top: 0, zIndex: 2,
                    textAlign: 'center', padding: '8px 10px', borderBottom: borderStyle,
                    background: 'var(--t-panel, var(--t-bg))',
                    minWidth: 150, verticalAlign: 'bottom',
                  }}>
                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                        <span
                          style={{ fontWeight: 600, color: 'var(--t-text-bright)', cursor: 'pointer', textDecoration: expandedGroupId === g.id ? 'underline' : 'none' }}
                          onClick={() => toggleMembersPanel(g.id)}
                          role="button"
                          tabIndex={0}
                          onKeyDown={(e) => e.key === 'Enter' && toggleMembersPanel(g.id)}
                          aria-label={`Toggle members for ${g.name}`}
                        >
                          {g.name}
                        </span>
                        {!g.is_default && (
                          <button
                            className="btn btn-ghost"
                            style={{ padding: '0 4px', fontSize: 11, lineHeight: 1 }}
                            onClick={() => deleteGroup(g.id)}
                            aria-label={`Delete group ${g.name}`}
                          >
                            ✕
                          </button>
                        )}
                      </div>
                      <div style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>
                        {g.member_count} member{g.member_count !== 1 ? 's' : ''}
                      </div>
                      {g.is_default && (
                        <span style={{ fontSize: 9, fontWeight: 700, color: 'var(--t-text-muted)', letterSpacing: 0.5, textTransform: 'uppercase' }}>
                          default
                        </span>
                      )}
                    </div>
                  </th>
                ))}
              </tr>
              {/* Column actions row */}
              <tr>
                <th style={{
                  position: 'sticky', left: 0, zIndex: 3,
                  padding: '4px 14px', borderBottom: borderStyle,
                  background: 'var(--t-panel, var(--t-bg))',
                  textAlign: 'left', fontSize: 11,
                }} />
                {groups.map((g: any) => (
                  <th key={g.id} style={{
                    textAlign: 'center', padding: '4px 6px', borderBottom: borderStyle,
                    background: 'var(--t-panel, var(--t-bg))', fontSize: 11,
                  }}>
                    <div style={{ display: 'flex', gap: 4, justifyContent: 'center', flexWrap: 'wrap' }}>
                      <button
                        className="btn btn-ghost"
                        style={{ padding: '1px 5px', fontSize: 10 }}
                        onClick={() => selectAllForGroup(g.id)}
                        aria-label={`Select all permissions for ${g.name}`}
                      >
                        All
                      </button>
                      <button
                        className="btn btn-ghost"
                        style={{ padding: '1px 5px', fontSize: 10 }}
                        onClick={() => clearAllForGroup(g.id)}
                        aria-label={`Clear all permissions for ${g.name}`}
                      >
                        None
                      </button>
                      <div style={{ position: 'relative' }}>
                        <button
                          className="btn btn-ghost"
                          style={{ padding: '1px 5px', fontSize: 10 }}
                          onClick={() => setCopyFromOpen(copyFromOpen === g.id ? null : g.id)}
                          aria-label={`Copy permissions from another group to ${g.name}`}
                        >
                          Copy...
                        </button>
                        {copyFromOpen === g.id && (
                          <div style={{
                            position: 'absolute', top: '100%', left: '50%', transform: 'translateX(-50%)',
                            background: 'var(--t-panel-alt, #1a1a2e)', border: borderStyle,
                            borderRadius: 6, padding: 4, zIndex: 10, minWidth: 120,
                            boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
                          }}>
                            {groups.filter((og: any) => og.id !== g.id).map((og: any) => (
                              <div
                                key={og.id}
                                style={{
                                  padding: '4px 10px', cursor: 'pointer', fontSize: 12,
                                  borderRadius: 4, color: 'var(--t-text)',
                                }}
                                className="hover-bg-surface"
                                onClick={() => copyFrom(g.id, og.id)}
                                role="button"
                                tabIndex={0}
                                onKeyDown={(e) => e.key === 'Enter' && copyFrom(g.id, og.id)}
                              >
                                {og.name}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                      {hasDefault(g.name) && (
                        <button
                          className="btn btn-ghost"
                          style={{ padding: '1px 5px', fontSize: 10 }}
                          onClick={() => resetToDefault(g)}
                          aria-label={`Reset ${g.name} to default permissions`}
                        >
                          Reset
                        </button>
                      )}
                    </div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {permsByCategory.flatMap(([cat, perms]) => [
                <tr key={`cat-${cat}`}>
                  <td
                    colSpan={1 + groups.length}
                    style={{
                      padding: '14px 14px 4px', fontWeight: 600, fontSize: 11,
                      color: 'var(--t-text-muted)', textTransform: 'uppercase',
                      letterSpacing: 0.5, background: 'var(--t-panel, var(--t-bg))',
                      position: 'sticky', left: 0,
                    }}
                  >
                    {cat}
                  </td>
                </tr>,
                ...perms.map((p: any) => (
                  <tr key={p.id}>
                    <td
                      style={{
                        position: 'sticky', left: 0, zIndex: 1,
                        padding: '7px 14px', borderBottom: borderStyle,
                        background: 'var(--t-panel, var(--t-bg))',
                        cursor: 'pointer', userSelect: 'none',
                      }}
                      onClick={() => toggleRow(p.id)}
                      role="button"
                      tabIndex={0}
                      onKeyDown={(e) => e.key === 'Enter' && toggleRow(p.id)}
                      aria-label={`Toggle ${p.label} for all groups`}
                      title={p.description || p.label}
                    >
                      <div style={{ fontWeight: 500, color: 'var(--t-text)', whiteSpace: 'nowrap', fontSize: 13 }}>
                        {p.label}
                      </div>
                      {p.description && (
                        <div style={{ fontSize: 11, color: 'var(--t-text-muted)', marginTop: 1 }}>
                          {p.description}
                        </div>
                      )}
                    </td>
                    {groups.map((g: any) => {
                      const checked = (matrix[String(g.id)] || []).includes(p.id);
                      const dirty = isCellDirty(g.id, p.id);
                      return (
                        <td
                          key={g.id}
                          style={{
                            textAlign: 'center', padding: '7px 10px', borderBottom: borderStyle,
                            cursor: 'pointer',
                            background: dirty ? 'rgba(59, 130, 246, 0.08)' : 'transparent',
                            transition: 'background 0.15s',
                          }}
                          onClick={() => toggleCell(g.id, p.id)}
                        >
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggleCell(g.id, p.id)}
                            onClick={(e) => e.stopPropagation()}
                            aria-label={`${p.label} for ${g.name}`}
                            style={{ cursor: 'pointer' }}
                          />
                        </td>
                      );
                    })}
                  </tr>
                )),
              ])}
            </tbody>
          </table>
        </div>
      </div>

      {/* Members panel (below matrix) */}
      {expandedGroupId && expandedGroup && (
        <div className="card" style={{ padding: 20, marginTop: 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--t-text-bright)', margin: 0 }}>
              Members — {expandedGroup.name}
            </h3>
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => setExpandedGroupId(null)}
              aria-label="Close members panel"
            >
              Close
            </button>
          </div>
          {membersLoading ? (
            <div style={{ opacity: 0.5, fontSize: 13 }}>Loading members...</div>
          ) : (
            <>
              <div style={{ marginBottom: 12 }}>
                <select
                  className="input"
                  style={{ width: 300 }}
                  value=""
                  onChange={(e) => { if (e.target.value) addMember(Number(e.target.value)); }}
                  aria-label={`Add member to ${expandedGroup.name}`}
                >
                  <option value="">Add member...</option>
                  {allUsers
                    .filter((u: any) => !members.some((m: any) => m.id === u.id) && u.role !== 'end_user')
                    .map((u: any) => (
                      <option key={u.id} value={u.id}>{u.name || u.email} ({u.role})</option>
                    ))
                  }
                </select>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {members.map((m: any) => (
                  <div key={m.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '6px 10px', background: 'var(--t-panel-alt)', borderRadius: 6 }}>
                    <div>
                      <span style={{ fontWeight: 500 }}>{m.name || m.email}</span>
                      <span style={{ fontSize: 12, opacity: 0.5, marginLeft: 8 }}>{m.role}</span>
                    </div>
                    <button className="btn btn-ghost" style={{ padding: '2px 6px', fontSize: 12 }} onClick={() => removeMember(m.id)} aria-label={`Remove ${m.name || m.email}`}>Remove</button>
                  </div>
                ))}
                {members.length === 0 && <div style={{ opacity: 0.5, fontSize: 13 }}>No members in this group</div>}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}


/* ============================================================
   TEAM MANAGER
   ============================================================ */
function TeamManager() {
  const [teams, setTeams] = useState<any[]>([]);
  const [allUsers, setAllUsers] = useState<any[]>([]);
  const [selectedTeam, setSelectedTeam] = useState<any>(null);
  const [members, setMembers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [newTeamName, setNewTeamName] = useState('');
  const [newTeamDesc, setNewTeamDesc] = useState('');
  const [createError, setCreateError] = useState('');
  const [saving, setSaving] = useState(false);

  const loadTeams = async () => {
    try { setTeams(await api.listTeams()); } catch {}
  };

  useEffect(() => {
    Promise.all([
      loadTeams(),
      api.listUsers().then(setAllUsers),
    ]).finally(() => setLoading(false));
  }, []);

  const selectTeam = async (t: any) => {
    setSelectedTeam(t);
    try { setMembers(await api.getTeamMembers(t.id)); } catch {}
  };

  const createTeam = async () => {
    if (!newTeamName.trim() || !newTeamDesc.trim()) return;
    setSaving(true);
    setCreateError('');
    try {
      await api.createTeam({ name: newTeamName.trim(), description: newTeamDesc.trim() });
      setNewTeamName('');
      setNewTeamDesc('');
      setShowCreate(false);
      await loadTeams();
    } catch (e: any) {
      setCreateError(e.message || 'Failed to create team');
    }
    setSaving(false);
  };

  const deleteTeam = async (id: number) => {
    if (!confirm('Delete this team? Tickets will be unassigned from it.')) return;
    await api.deleteTeam(id);
    if (selectedTeam?.id === id) setSelectedTeam(null);
    await loadTeams();
  };

  const addMember = async (userId: number) => {
    if (!selectedTeam) return;
    const updated = [...members.map(m => ({ user_id: m.user_id, role: m.role })), { user_id: userId, role: 'member' }];
    await api.updateTeamMembers(selectedTeam.id, updated);
    setMembers(await api.getTeamMembers(selectedTeam.id));
    await loadTeams();
  };

  const removeMember = async (userId: number) => {
    if (!selectedTeam) return;
    const updated = members.filter(m => m.user_id !== userId).map(m => ({ user_id: m.user_id, role: m.role }));
    await api.updateTeamMembers(selectedTeam.id, updated);
    setMembers(await api.getTeamMembers(selectedTeam.id));
    await loadTeams();
  };

  const toggleLead = async (userId: number) => {
    if (!selectedTeam) return;
    const updated = members.map(m => ({
      user_id: m.user_id,
      role: m.user_id === userId ? (m.role === 'lead' ? 'member' : 'lead') : m.role,
    }));
    await api.updateTeamMembers(selectedTeam.id, updated);
    setMembers(await api.getTeamMembers(selectedTeam.id));
  };

  const updateLead = async (leadId: number | null) => {
    if (!selectedTeam) return;
    await api.updateTeam(selectedTeam.id, { lead_id: leadId });
    await loadTeams();
    setSelectedTeam({ ...selectedTeam, lead_id: leadId });
  };

  if (loading) return <div className="loading-text">Loading teams...</div>;

  const availableUsers = allUsers.filter(u => !members.some(m => m.user_id === u.id) && u.role !== 'end_user');

  return (
    <div style={{ display: 'flex', gap: 24, minHeight: 500 }}>
      {/* Left: Team list */}
      <div style={{ width: 260, flexShrink: 0 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--t-text-bright)', margin: 0 }}>Teams</h3>
          <button className="btn btn-primary btn-sm" onClick={() => setShowCreate(!showCreate)}>
            {showCreate ? 'Cancel' : '+ New'}
          </button>
        </div>
        {showCreate && (
          <div className="card" style={{ padding: 10, marginBottom: 12 }}>
            <input
              className="form-input"
              placeholder="Team name"
              value={newTeamName}
              onChange={(e) => setNewTeamName(e.target.value)}
              style={{ marginBottom: 6, fontSize: 12 }}
              autoFocus
            />
            <textarea
              className="form-input"
              placeholder="Description (required — Atlas uses this for auto-routing)"
              value={newTeamDesc}
              onChange={(e) => setNewTeamDesc(e.target.value)}
              style={{ marginBottom: 6, fontSize: 12, minHeight: 50 }}
            />
            {createError && <div className="form-error" style={{ marginBottom: 6 }}>{createError}</div>}
            <button
              className="btn btn-primary btn-sm"
              onClick={createTeam}
              disabled={saving || !newTeamName.trim() || !newTeamDesc.trim()}
              style={{ width: '100%' }}
            >
              {saving ? 'Creating...' : 'Create Team'}
            </button>
          </div>
        )}
        {teams.map((t) => (
          <div
            key={t.id}
            className={`card ${selectedTeam?.id === t.id ? 'card-active' : ''}`}
            style={{ padding: '10px 14px', marginBottom: 6, cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
            onClick={() => selectTeam(t)}
          >
            <div>
              <div style={{ fontWeight: 600, fontSize: 14 }}>{t.name}</div>
              <div style={{ fontSize: 11, color: 'var(--t-text-dim)' }}>
                {t.member_count} member{t.member_count !== 1 ? 's' : ''} · {t.open_ticket_count} open ticket{t.open_ticket_count !== 1 ? 's' : ''}
              </div>
            </div>
            <button
              className="btn btn-sm btn-danger"
              style={{ padding: '2px 6px', fontSize: 11 }}
              onClick={(e) => { e.stopPropagation(); deleteTeam(t.id); }}
            >&times;</button>
          </div>
        ))}
        {teams.length === 0 && (
          <div style={{ fontSize: 12, color: 'var(--t-text-dim)', fontStyle: 'italic' }}>
            No teams yet. Create one to organize your agents.
          </div>
        )}
      </div>

      {/* Right: Team detail */}
      <div style={{ flex: 1 }}>
        {selectedTeam ? (
          <>
            <h3 style={{ fontSize: 16, fontWeight: 600, color: 'var(--t-text-bright)', margin: '0 0 4px' }}>{selectedTeam.name}</h3>
            <div style={{ fontSize: 11, color: 'var(--t-text-dim)', marginBottom: 16 }}>
              {selectedTeam.lead_name ? `Lead: ${selectedTeam.lead_name}` : 'No lead assigned'}
            </div>

            <div style={{ marginBottom: 20 }}>
              <h4 style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Members ({members.length})</h4>
              {members.map((m) => (
                <div key={m.user_id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0', borderBottom: '1px solid var(--t-border)' }}>
                  <span style={{ flex: 1 }}>
                    <span style={{ fontWeight: 500 }}>{m.name}</span>
                    <span style={{ fontSize: 11, color: 'var(--t-text-dim)', marginLeft: 6 }}>{m.email}</span>
                  </span>
                  <button
                    className={`btn btn-sm ${m.role === 'lead' ? 'btn-primary' : 'btn-ghost'}`}
                    style={{ padding: '2px 8px', fontSize: 10 }}
                    onClick={() => { toggleLead(m.user_id); updateLead(m.role === 'lead' ? null : m.user_id); }}
                    title={m.role === 'lead' ? 'Remove lead role' : 'Set as team lead'}
                  >
                    {m.role === 'lead' ? 'Lead' : 'Set Lead'}
                  </button>
                  <button
                    className="btn btn-sm btn-ghost"
                    style={{ padding: '2px 6px', fontSize: 11, color: 'var(--c-danger)' }}
                    onClick={() => removeMember(m.user_id)}
                  >Remove</button>
                </div>
              ))}
            </div>

            {availableUsers.length > 0 && (
              <div>
                <h4 style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Add Member</h4>
                <select
                  className="form-input"
                  style={{ maxWidth: 300 }}
                  onChange={(e) => { if (e.target.value) addMember(Number(e.target.value)); e.target.value = ''; }}
                  defaultValue=""
                >
                  <option value="">Select a user...</option>
                  {availableUsers.map((u) => (
                    <option key={u.id} value={u.id}>{u.name} ({u.email})</option>
                  ))}
                </select>
              </div>
            )}
          </>
        ) : (
          <div className="empty-state" style={{ marginTop: 60 }}>
            <div className="empty-state-title">Select a team</div>
            <div className="empty-state-text">Choose a team from the left to manage its members.</div>
          </div>
        )}
      </div>
    </div>
  );
}


/* ============================================================
   BRANDING SETTINGS
   ============================================================ */
function BrandingSettings() {
  const tenantId   = useAuthStore((s) => s.user?.tenant_id);
  const isSA       = useAuthStore((s) => s.isSuperAdmin);
  const cfg        = window.__APP_CONFIG__ as any;
  const tenantSettings = cfg?.tenant_settings || {};

  const [appName,       setAppName]       = useState(tenantSettings.app_name || cfg?.app_name || '');
  const [logoUrl,       setLogoUrl]       = useState(tenantSettings.logo_url || cfg?.tenant_logo_url || '');
  const [appUrl,        setAppUrl]        = useState(tenantSettings.app_url || cfg?.app_url || '');
  const [inboundDomain, setInboundDomain] = useState(tenantSettings.inbound_email_domain || '');
  const [emailFromName, setEmailFromName] = useState(cfg?.email_from_name || '');
  const [ticketPrefix,  setTicketPrefix]  = useState('');
  const [prefixError,   setPrefixError]   = useState('');
  const [saving, setSaving] = useState(false);
  const [saved,  setSaved]  = useState(false);

  // Fetch the current ticket_prefix from the tenants list (available to super_admin)
  useEffect(() => {
    if (!isSA() || !tenantId) return;
    api.listTenants()
      .then((tenants: any[]) => {
        const match = tenants.find((t: any) => t.id === tenantId);
        if (match?.ticket_prefix) setTicketPrefix(match.ticket_prefix);
      })
      .catch(() => {/* best-effort — field stays empty */});
  }, [tenantId]);

  const handlePrefixChange = (raw: string) => {
    const upper = raw.toUpperCase().replace(/[^A-Z0-9\-]/g, '');
    setTicketPrefix(upper);
    if (upper.length > 20) {
      setPrefixError('Maximum 20 characters');
    } else {
      setPrefixError('');
    }
  };

  const save = async () => {
    if (!tenantId) return;
    if (prefixError) return;
    setSaving(true);
    try {
      const payload: Record<string, string | null> = {
        app_name:             appName || null,
        logo_url:             logoUrl || null,
        app_url:              appUrl || null,
        inbound_email_domain: inboundDomain || null,
        email_from_name:      emailFromName || null,
      };
      if (ticketPrefix) payload.ticket_prefix = ticketPrefix;
      await api.updateTenantSettings(tenantId, payload);
      // Update runtime config so sidebar reflects immediately
      if (cfg) {
        cfg.app_name = appName || cfg.app_name;
        cfg.tenant_logo_url = logoUrl || cfg.tenant_logo_url;
        cfg.tenant_settings = { ...cfg.tenant_settings, app_name: appName, logo_url: logoUrl, app_url: appUrl, inbound_email_domain: inboundDomain };
      }
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ maxWidth: 600 }}>
      <h3 style={{ fontSize: 16, fontWeight: 600, color: 'var(--t-text-bright)', margin: '0 0 4px' }}>
        Branding &amp; Identity
      </h3>
      <p style={{ fontSize: 12, color: 'var(--t-text-muted)', margin: '0 0 24px' }}>
        Customise how your organisation appears to agents and end-users. These values override the platform defaults for your tenant.
      </p>

      {/* Logo preview + fields */}
      <div style={{ display: 'flex', gap: 20, marginBottom: 24 }}>
        <div style={{
          width: 80, height: 80, borderRadius: 'var(--radius-sm)',
          background: 'var(--t-input-bg)', display: 'flex', alignItems: 'center', justifyContent: 'center',
          overflow: 'hidden', flexShrink: 0, border: '1px solid var(--t-border)',
        }}>
          {logoUrl ? (
            <img src={logoUrl} alt="" style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
          ) : (
            <span style={{ fontSize: 32, fontWeight: 700, color: 'var(--t-text-dim)' }}>
              {(appName || 'H').charAt(0).toUpperCase()}
            </span>
          )}
        </div>
        <div style={{ flex: 1 }}>
          <div className="form-group" style={{ marginBottom: 12 }}>
            <label className="form-label">Organisation Name</label>
            <input
              className="form-input"
              value={appName}
              onChange={(e) => setAppName(e.target.value)}
              placeholder="e.g. Acme Corp Helpdesk"
            />
            <div style={{ fontSize: 11, color: 'var(--t-text-dim)', marginTop: 4 }}>
              Shown in sidebar, emails, and notifications
            </div>
          </div>
          <div className="form-group">
            <label className="form-label">Logo URL</label>
            <input
              className="form-input"
              value={logoUrl}
              onChange={(e) => setLogoUrl(e.target.value)}
              placeholder="https://example.com/logo.png"
            />
            <div style={{ fontSize: 11, color: 'var(--t-text-dim)', marginTop: 4 }}>
              Square image works best (PNG or SVG, ~200px)
            </div>
          </div>
        </div>
      </div>

      {/* URL settings */}
      <div className="form-group" style={{ marginBottom: 16 }}>
        <label className="form-label">Application URL</label>
        <input
          className="form-input"
          value={appUrl}
          onChange={(e) => setAppUrl(e.target.value)}
          placeholder="https://helpdesk.example.com"
        />
        <div style={{ fontSize: 11, color: 'var(--t-text-dim)', marginTop: 4 }}>
          Used in email links and webhook notifications. Leave blank to use the platform default.
        </div>
      </div>

      <div className="form-group" style={{ marginBottom: 24 }}>
        <label className="form-label">Inbound Email Domain</label>
        <input
          className="form-input"
          value={inboundDomain}
          onChange={(e) => setInboundDomain(e.target.value)}
          placeholder="e.g. support.example.com"
        />
        <div style={{ fontSize: 11, color: 'var(--t-text-dim)', marginTop: 4 }}>
          {inboundDomain ? (
            <>Customers can create tickets by emailing <code style={{ color: 'var(--t-accent)', fontFamily: 'var(--mono)' }}>{window.location.pathname.split('/')[1]}@{inboundDomain}</code>. This is also used as the reply-to on outbound emails.</>
          ) : (
            <>Set a domain to enable inbound email-to-ticket. Customers will email <code style={{ color: 'var(--t-text-muted)', fontFamily: 'var(--mono)' }}>your-slug@your-domain.com</code> to create tickets.</>
          )}
        </div>
      </div>

      <div className="form-group" style={{ marginBottom: 24 }}>
        <label className="form-label">Email Sender Name</label>
        <input
          className="form-input"
          value={emailFromName}
          onChange={(e) => setEmailFromName(e.target.value)}
          placeholder={appName || 'e.g. Acme IT Support'}
        />
        <div style={{ fontSize: 11, color: 'var(--t-text-dim)', marginTop: 4 }}>
          Name shown in recipient inboxes. Defaults to your organisation name when left blank.
        </div>
      </div>

      {/* Ticket Prefix */}
      <div className="form-group" style={{ marginBottom: 24 }}>
        <label className="form-label">Ticket Prefix</label>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <input
            className="form-input"
            value={ticketPrefix}
            onChange={(e) => handlePrefixChange(e.target.value)}
            placeholder="e.g. ACME"
            maxLength={20}
            style={{ maxWidth: 160, textTransform: 'uppercase', fontFamily: 'var(--mono, monospace)', letterSpacing: '0.04em' }}
          />
          {ticketPrefix && (
            <span style={{ fontSize: 12, color: 'var(--t-text-muted)', fontFamily: 'var(--mono, monospace)' }}>
              → {ticketPrefix}-00001
            </span>
          )}
        </div>
        {prefixError && (
          <div style={{ fontSize: 11, color: 'var(--t-text-danger, #e74c3c)', marginTop: 4 }}>
            {prefixError}
          </div>
        )}
        <div style={{ fontSize: 11, color: 'var(--t-text-dim)', marginTop: 4 }}>
          Used to prefix ticket numbers (e.g. ACME-00001). Letters, numbers, and hyphens only. Max 20 characters.
        </div>
      </div>

      <button className="btn btn-primary" onClick={save} disabled={saving || !!prefixError}>
        {saving ? 'Saving...' : saved ? 'Saved' : 'Save Branding'}
      </button>
    </div>
  );
}


/* ============================================================
   PORTAL SETTINGS WITH LIVE PREVIEW
   ============================================================ */
function PortalSettingsWithPreview() {
  const tenantId = useAuthStore((s) => s.user?.tenant_id);
  const tenantSettings = (window.__APP_CONFIG__ as any)?.tenant_settings || {};
  const tenantSlug = (window.__APP_CONFIG__ as any)?.tenant_slug;

  const [greeting, setGreeting] = useState(tenantSettings.portal_greeting || 'How can we help you today?');
  const [background, setBackground] = useState(tenantSettings.portal_background || 'gradient-indigo');
  const [cards, setCards] = useState<PortalCard[]>(tenantSettings.portal_cards || DEFAULT_PORTAL_CARDS);
  const [cardOpacity, setCardOpacity] = useState(tenantSettings.portal_card_opacity ?? 70);
  const [logoUrl, setLogoUrl] = useState(tenantSettings.portal_logo_url || '');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [showSettings, setShowSettings] = useState(true);
  const userRole = useAuthStore((s) => s.user?.role);
  const [allowedDomains, setAllowedDomains] = useState('');
  const [domainsSaving, setDomainsSaving] = useState(false);
  const [domainsSaved, setDomainsSaved] = useState(false);

  useEffect(() => {
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
        portal_card_opacity: cardOpacity,
        portal_logo_url: logoUrl || undefined,
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
    return <div style={{ padding: 40, textAlign: 'center', color: 'var(--t-text-muted)' }}>Select a tenant to configure portal settings.</div>;
  }

  return (
    <div style={showSettings ? { marginRight: 390, transition: 'margin-right .2s ease' } : { transition: 'margin-right .2s ease' }}>
      {/* Toolbar */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--t-text-bright)', margin: 0 }}>
          Portal — Live Preview
        </h3>
        <div style={{ display: 'flex', gap: 8 }}>
          {!showSettings && (
            <button className="btn btn-primary btn-sm" onClick={() => setShowSettings(true)}>
              Edit Settings
            </button>
          )}
          {tenantSlug && (
            <a
              href={`/${tenantSlug}/portal`}
              target="_blank"
              rel="noopener noreferrer"
              className="btn btn-ghost btn-sm"
              style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
            >
              Open Portal &#8599;
            </a>
          )}
        </div>
      </div>

      {/* Live Preview */}
      <div className="portal-preview-frame">
        <div className="portal-landing-wrapper portal-preview-inner" style={{ '--portal-glass-opacity': (cardOpacity / 100).toFixed(2) } as React.CSSProperties}>
          {logoUrl && (
            <div className="portal-logo-overlay">
              <img src={logoUrl} alt="" />
            </div>
          )}
          <div className="portal-landing">
            <PortalHero greeting={greeting} background={background} onSearch={() => {}} />
            <PortalCardGrid cards={cards} aiChatEnabled={true} onAction={() => {}} cardOpacity={cardOpacity} />
          </div>
        </div>
      </div>

      {/* Settings Slideout Panel */}
      {showSettings && (
        <div className="portal-settings-slideout" onClick={(e) => e.stopPropagation()}>
          <div className="slideout-header">
            <div className="slideout-title">Portal Settings</div>
            <button className="btn btn-ghost btn-xs" onClick={() => setShowSettings(false)} style={{ fontSize: 16 }}>&#10005;</button>
          </div>
          <div className="slideout-body">
            {/* Allowed Domains — admin-only access control */}
            {(userRole === 'tenant_admin' || userRole === 'super_admin') && (
              <div style={{ marginBottom: 20, padding: '12px 14px', background: 'var(--t-surface-2)', borderRadius: 8, border: '1px solid var(--t-border)' }}>
                <label className="form-label" style={{ marginBottom: 4 }}>Allowed Email Domains</label>
                <p style={{ fontSize: 11, color: 'var(--t-text-muted)', marginBottom: 10, lineHeight: 1.5 }}>
                  Users from these domains are auto-provisioned as end-users via OAuth.
                  Comma-separated (e.g. <code style={{ fontSize: 10, padding: '1px 3px', background: 'var(--t-surface-3)', borderRadius: 3 }}>acme.com,acme-corp.com</code>).
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
                  <button className="btn btn-primary btn-xs" onClick={handleSaveDomains} disabled={domainsSaving}>
                    {domainsSaving ? '...' : 'Save'}
                  </button>
                </div>
                {domainsSaved && <span style={{ fontSize: 11, color: 'var(--t-success)', marginTop: 4, display: 'block' }}>Saved!</span>}
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
            <div className="form-group" style={{ marginBottom: 20 }}>
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

            {/* Card Opacity */}
            <div className="form-group" style={{ marginBottom: 20 }}>
              <label className="form-label">Portal Transparency</label>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <span style={{ fontSize: 11, color: 'var(--t-text-dim)', minWidth: 28 }}>0%</span>
                <input
                  type="range"
                  min={0}
                  max={100}
                  value={cardOpacity}
                  onChange={(e) => setCardOpacity(Number(e.target.value))}
                  style={{ flex: 1 }}
                />
                <span style={{ fontSize: 11, color: 'var(--t-text-dim)', minWidth: 28, textAlign: 'right' }}>100%</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--t-text-dim)', marginTop: 2 }}>
                <span>Invisible</span>
                <span>Solid</span>
              </div>
            </div>

            {/* Logo */}
            <div className="form-group" style={{ marginBottom: 20 }}>
              <label className="form-label">Brand Logo</label>
              <input
                className="form-input"
                type="text"
                value={logoUrl}
                onChange={(e) => setLogoUrl(e.target.value)}
                placeholder="https://your-domain.com/logo.png"
              />
              <div style={{ fontSize: 10, color: 'var(--t-text-dim)', marginTop: 4 }}>
                Optional. Displayed as a watermark behind the portal. Leave empty for hex grid only.
              </div>
              {logoUrl && (
                <div style={{ marginTop: 8, padding: 12, background: 'var(--t-panel-alt)', borderRadius: 8, textAlign: 'center' }}>
                  <img src={logoUrl} alt="Logo preview" style={{ maxWidth: 120, maxHeight: 60, objectFit: 'contain', opacity: 0.7 }} onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }} />
                </div>
              )}
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
                        style={{ width: 110 }}
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
                        style={{ width: 120 }}
                      >
                        {ACTION_OPTIONS.map((a) => (
                          <option key={a.value} value={a.value}>{a.label}</option>
                        ))}
                      </select>
                    </div>
                    <div className="portal-card-editor-fields">
                      <textarea
                        className="form-input"
                        value={card.description}
                        onChange={(e) => updateCard(i, { description: e.target.value })}
                        placeholder="Description"
                        rows={3}
                        style={{ flex: 1, resize: 'vertical', minHeight: 60, fontFamily: 'inherit', fontSize: 12 }}
                      />
                      {card.action === 'url' && (
                        <input
                          className="form-input"
                          type="text"
                          value={card.url || ''}
                          onChange={(e) => updateCard(i, { url: e.target.value })}
                          placeholder="https://..."
                          style={{ width: 160 }}
                        />
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
                {saving ? 'Saving...' : 'Save Settings'}
              </button>
              {saved && <span style={{ fontSize: 12, color: 'var(--t-success)' }}>Saved!</span>}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}


/* ============================================================
   LOCATIONS
   ============================================================ */
type LocationPanel = 'tree' | 'import' | 'dbsync';

function LocationManager() {
  const { locations, loadLocations, createLocation, updateLocation, deleteLocation } = useHierarchyStore();
  const [panel, setPanel] = useState<LocationPanel>('tree');

  useEffect(() => {
    loadLocations();
  }, []);

  const toggle = (p: LocationPanel) => setPanel((cur) => cur === p ? 'tree' : p);

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginBottom: 12 }}>
        <button className="btn btn-sm btn-ghost" onClick={() => window.open('/api/hierarchies/locations/export', '_blank')}>
          Export
        </button>
        <button className={`btn btn-sm ${panel === 'dbsync' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => toggle('dbsync')}>
          DB Sync
        </button>
        <button className={`btn btn-sm ${panel === 'import' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => toggle('import')}>
          Import
        </button>
      </div>
      {panel === 'dbsync' && <LocationDbSync />}
      {panel === 'import' && <LocationImport onClose={() => setPanel('tree')} />}
      {panel === 'tree' && (
        <>
          <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--t-text-bright)', margin: '0 0 12px' }}>
            Location Hierarchy
          </h3>
          <TierView
            items={locations}
            onCreate={async (data) => {
              await createLocation(data);
            }}
            onUpdate={updateLocation}
            onDelete={deleteLocation}
            showContactInfo
          />
        </>
      )}
    </div>
  );
}


/* ============================================================
   CATEGORY IMPORT (drag-and-drop, same pattern as LocationImport)
   ============================================================ */
function CategoryImportPanel({ onImported, onClose }: { onImported: () => void; onClose: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string[][]>([]);
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState<{ created: number; skipped: number } | null>(null);
  const [error, setError] = useState('');
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const handleFile = (f: File) => {
    setFile(f);
    setResult(null);
    setError('');
    if (f.name.toLowerCase().endsWith('.csv')) {
      const reader = new FileReader();
      reader.onload = (e) => {
        const text = e.target?.result as string;
        const lines = text.split('\n').filter(l => l.trim());
        const rows = lines.slice(0, 11).map(l => {
          const result: string[] = [];
          let current = '';
          let inQuotes = false;
          for (const ch of l) {
            if (ch === '"') { inQuotes = !inQuotes; continue; }
            if (ch === ',' && !inQuotes) { result.push(current.trim()); current = ''; continue; }
            current += ch;
          }
          result.push(current.trim());
          return result;
        });
        setPreview(rows);
      };
      reader.readAsText(f);
    } else {
      setPreview([]);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const f = e.dataTransfer.files[0];
    if (f) handleFile(f);
  };

  const handleImport = async () => {
    if (!file) return;
    setImporting(true);
    setError('');
    try {
      const res = await api.importProblemCategories(file);
      setResult(res);
      onImported();
    } catch (e: any) {
      setError(e.message || 'Import failed');
    }
    setImporting(false);
  };

  return (
    <div className="import-panel">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h4 style={{ fontSize: 14, fontWeight: 600, color: 'var(--t-text-bright)', margin: 0 }}>
          Import Categories
        </h4>
        <button className="btn btn-sm btn-ghost" onClick={onClose}>Close</button>
      </div>

      <p style={{ fontSize: 12, color: 'var(--t-text-muted)', marginBottom: 8 }}>
        Upload a CSV or Excel (.xlsx) file. Columns are interpreted as hierarchy tiers left to right,
        with an optional Severity column for default priority (Sev-1 = p1, Sev-2 = p2, etc.).
      </p>
      <div style={{ marginBottom: 16 }}>
        <a
          href="/api/hierarchies/problem-categories/template"
          download
          style={{ fontSize: 12, color: 'var(--t-accent)', textDecoration: 'underline', cursor: 'pointer' }}
        >
          Download Template
        </a>
      </div>

      <div
        className={`import-dropzone ${dragging ? 'dragging' : ''}`}
        onClick={() => fileRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
      >
        {file ? (
          <span>{file.name} ({(file.size / 1024).toFixed(1)} KB)</span>
        ) : (
          <span>Drop file here or click to browse<br />
            <small style={{ color: 'var(--t-text-dim)' }}>.csv, .xlsx</small>
          </span>
        )}
      </div>
      <input
        ref={fileRef}
        type="file"
        accept=".csv,.xlsx,.xls"
        style={{ display: 'none' }}
        onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])}
      />

      {preview.length > 0 && (
        <div className="import-preview">
          <table>
            <thead>
              <tr>
                {preview[0].map((h, i) => (
                  <th key={i}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {preview.slice(1).map((row, ri) => (
                <tr key={ri}>
                  {row.map((cell, ci) => (
                    <td key={ci}>{cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{ fontSize: 11, color: 'var(--t-text-dim)', marginTop: 4 }}>
            Showing first {preview.length - 1} rows
          </div>
        </div>
      )}

      <div style={{ display: 'flex', gap: 8, marginTop: 16, alignItems: 'center' }}>
        <button
          className="btn btn-primary"
          onClick={handleImport}
          disabled={!file || importing}
        >
          {importing ? 'Importing...' : 'Import'}
        </button>
        {error && <span style={{ color: 'var(--t-error)', fontSize: 12 }}>{error}</span>}
      </div>

      {result && (
        <div className="import-result">
          Import complete: {result.created} created, {result.skipped} skipped (duplicates).
        </div>
      )}
    </div>
  );
}


/* ============================================================
   CATEGORIES
   ============================================================ */
type CategoryPanel = 'tier' | 'import' | 'dbsync';

function ProblemCategoryManager() {
  const { problemCategories, loadProblemCategories, createProblemCategory, updateProblemCategory, deleteProblemCategory } = useHierarchyStore();
  const [panel, setPanel] = useState<CategoryPanel>('tier');
  const [teams, setTeams] = useState<any[]>([]);

  useEffect(() => {
    loadProblemCategories();
    api.listTeams().then(setTeams).catch(() => {});
  }, []);

  const tenantSettings = (window.__APP_CONFIG__ as any)?.tenant_settings || {};
  const problemFieldLabel = tenantSettings.problem_field_label || 'Problem Category';

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--t-text-bright)', margin: 0 }}>
          {problemFieldLabel} Hierarchy
        </h3>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-sm btn-ghost" onClick={() => window.open('/api/hierarchies/problem-categories/export', '_blank')}>
            Export
          </button>
          <button
            className={`btn btn-sm ${panel === 'import' ? 'btn-primary' : 'btn-ghost'}`}
            onClick={() => setPanel(panel === 'import' ? 'tier' : 'import')}
          >
            Import
          </button>
          <button
            className={`btn btn-sm ${panel === 'dbsync' ? 'btn-primary' : 'btn-ghost'}`}
            onClick={() => setPanel(panel === 'dbsync' ? 'tier' : 'dbsync')}
          >
            DB Sync
          </button>
        </div>
      </div>

      {panel === 'import' && (
        <CategoryImportPanel
          onImported={() => { loadProblemCategories(); setTimeout(() => setPanel('tier'), 2000); }}
          onClose={() => setPanel('tier')}
        />
      )}

      {panel === 'tier' && (
        <TierView
          items={problemCategories}
          onCreate={createProblemCategory}
          onUpdate={updateProblemCategory}
          onDelete={deleteProblemCategory}
          showPriority
        />
      )}
      {panel === 'dbsync' && <CategoryDbSync />}
    </div>
  );
}


/* ============================================================
   TENANTS
   ============================================================ */
const MODULE_ICONS: Record<string, string> = {
  utensils: '🍴', video: '📹', server: '🖥', 'bar-chart': '📊',
  layout: '🖼', phone: '📞', monitor: '💻', speaker: '🔊',
  'credit-card': '💳', zap: '⚡', square: '🟦', database: '🗄',
  mail: '✉️', users: '👥', table: '📋', folder: '📂',
  'shopping-cart': '🛒', music: '🎵', 'message-circle': '💬',
  'file-text': '📄', gift: '🎁',
};

type ModuleCategory = 'pos' | 'office' | 'operations' | 'finance';

const MODULE_CATEGORY_META: Record<ModuleCategory, { label: string; color: string }> = {
  pos:        { label: 'POS Systems',   color: '#e74c3c' },
  office:     { label: 'Office & Analytics', color: '#3498db' },
  operations: { label: 'Operations',    color: '#2ecc71' },
  finance:    { label: 'Finance',       color: '#f39c12' },
};

const MODULE_CATEGORIES: Record<string, ModuleCategory> = {
  toast: 'pos', shift4: 'pos', lightspeed: 'pos', square: 'pos',
  oracle_simphony: 'pos', oracle_xstore: 'pos',
  ms_outlook: 'office', ms_teams: 'office', ms_excel: 'office',
  ms_sharepoint: 'office', powerbi: 'office',
  solink: 'operations', olo: 'operations', rockbot: 'operations',
  r365: 'operations', sonos: 'operations',
  bill_com: 'finance', paytronix: 'finance',
};

let _cachedTenants: Tenant[] = [];

interface PlanData {
  plan_tier: string;
  plan_expires_at: string | null;
  plan_extended_by_name: string | null;
  plan_extended_at: string | null;
}

/* ============================================================
   TENANT DETAIL — tabbed panel shown when a tenant is selected
   ============================================================ */
type TenantDetailTab = 'overview' | 'modules' | 'plan' | 'settings';

interface TenantDetailProps {
  tenant: Tenant;
  modules: (KnowledgeModule & { enabled: boolean })[];
  aiFeatures: ModuleFeature[];
  expandedModule: number | null;
  setExpandedModule: (id: number | null) => void;
  toggleModule: (moduleId: number, enabled: boolean) => void;
  toggleFeature: (featureId: number, enabled: boolean) => void;
  plan: PlanData | null;
  planTier: string;
  setPlanTier: (t: string) => void;
  extendDays: string;
  setExtendDays: (d: string) => void;
  planSaving: boolean;
  planSaved: boolean;
  onSavePlan: () => void;
  onNavigateTab: (tab: Tab) => void;
}

function TenantDetail({
  tenant, modules, aiFeatures, expandedModule, setExpandedModule,
  toggleModule, toggleFeature,
  plan, planTier, setPlanTier, extendDays, setExtendDays,
  planSaving, planSaved, onSavePlan,
  onNavigateTab,
}: TenantDetailProps) {
  const [activeTab, setActiveTab] = useState<TenantDetailTab>('overview');

  const knowledgeModules = modules.filter((m) => (m.module_type || 'knowledge') === 'knowledge');
  const featureModules = modules.filter((m) => m.module_type === 'feature');

  const DETAIL_TABS: { key: TenantDetailTab; label: string }[] = [
    { key: 'overview', label: 'Overview' },
    { key: 'modules',  label: 'Modules' },
    { key: 'plan',     label: 'Plan' },
    { key: 'settings', label: 'Settings' },
  ];

  const SETTINGS_CARDS: { icon: string; title: string; description: string; tab: Tab }[] = [
    { icon: '📞', title: 'Communications',  description: 'Voice agents, SMS, WhatsApp, IVR routing, and messaging.', tab: 'phone' },
    { icon: '🌐', title: 'Portal',          description: 'Customize portal branding, hero text, and cards.',        tab: 'portal' },
    { icon: '🎨', title: 'Branding',        description: 'Set tenant logo, color scheme, and app name.',            tab: 'branding' },
    { icon: '🔔', title: 'Notifications',   description: 'Configure notification groups and event subscriptions.',  tab: 'notifications' },
  ];

  // KB filter state lives here so it resets when tenant changes
  const [kbFilter, setKbFilter] = useState<ModuleCategory | 'all' | 'enabled'>('all');
  const [kbSearch, setKbSearch] = useState('');

  const isKnowledgeModule = (m: KnowledgeModule) => (m.module_type || 'knowledge') === 'knowledge' && m.slug !== 'ai';
  const canExpand = (m: KnowledgeModule & { enabled: boolean }) =>
    m.slug === 'ai' ? m.enabled : m.slug === 'customer_portal' ? m.enabled : m.slug === 'phone_support' ? m.enabled : isKnowledgeModule(m);

  const renderModuleGrid = (mods: (KnowledgeModule & { enabled: boolean })[]) => (
    <div className="admin-grid">
      {mods.map((m) => {
        const docCount = (m as any).doc_count ?? 0;
        const chunkCount = (m as any).chunk_count ?? 0;
        const isKB = isKnowledgeModule(m);
        const isExpanded = expandedModule === m.id;
        const ratio = docCount > 0 ? (chunkCount / docCount).toFixed(1) : '—';
        // Determine if this feature module has a corresponding settings tab
        const featureTabLink: Tab | null =
          m.slug === 'phone_support' ? 'phone' :
          m.slug === 'customer_portal' ? 'portal' :
          null;

        return (
          <div key={m.id} style={{ display: 'flex', flexDirection: 'column', gap: 0, height: '100%' }}>
            <div className="module-card" style={{ flex: 1 }}>
              <div className="module-card-header"
                   style={{ cursor: canExpand(m) ? 'pointer' : undefined, width: '100%' }}
                   onClick={() => {
                     if (canExpand(m)) { setExpandedModule(isExpanded ? null : m.id); }
                   }}>
                <div className="module-card-info">
                  <div className="module-card-icon">
                    {MODULE_ICONS[m.icon] || '📦'}
                  </div>
                  <div style={{ flex: 1 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span className="module-card-name">{m.name}</span>
                      {isKB && MODULE_CATEGORIES[m.slug] && (
                        <span style={{
                          fontSize: 9, padding: '1px 6px', borderRadius: 8,
                          background: `${MODULE_CATEGORY_META[MODULE_CATEGORIES[m.slug]].color}22`,
                          color: MODULE_CATEGORY_META[MODULE_CATEGORIES[m.slug]].color,
                          fontWeight: 500, lineHeight: '16px', whiteSpace: 'nowrap',
                        }}>
                          {MODULE_CATEGORY_META[MODULE_CATEGORIES[m.slug]].label}
                        </span>
                      )}
                      {featureTabLink && m.enabled && (
                        <button
                          className="btn btn-ghost btn-sm"
                          style={{ fontSize: 10, padding: '1px 8px', marginLeft: 2 }}
                          onClick={(e) => { e.stopPropagation(); onNavigateTab(featureTabLink); }}
                        >
                          Configure →
                        </button>
                      )}
                    </div>
                    <div className="module-card-desc">{m.description}</div>
                    {isKB && (
                      <div style={{ display: 'flex', gap: 12, marginTop: 6, fontSize: 11 }}>
                        <span style={{ color: docCount > 0 ? 'var(--t-accent)' : 'var(--t-text-danger, #e74c3c)' }}>
                          {docCount.toLocaleString()} docs
                        </span>
                        <span style={{ color: chunkCount > 0 ? 'var(--t-accent)' : 'var(--t-text-danger, #e74c3c)' }}>
                          {chunkCount.toLocaleString()} chunks
                        </span>
                        {docCount > 0 && (
                          <span style={{ color: 'var(--t-text-muted)' }}>
                            {ratio} chunks/doc
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
                  {canExpand(m) && (
                    <span style={{ fontSize: 10, color: 'var(--t-text-muted)', transition: 'transform 0.2s', transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)' }}>&#9654;</span>
                  )}
                  <label className="toggle" onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={m.enabled}
                      onChange={() => toggleModule(m.id, m.enabled)}
                    />
                    <div className="toggle-track" />
                    <div className="toggle-thumb" />
                  </label>
                </div>
              </div>
            </div>

            {/* KB module detail panel */}
            {isKB && isExpanded && (
              <div style={{ padding: '12px 16px', background: 'var(--t-panel)', border: '1px solid var(--t-border)', borderTop: 'none', borderBottomLeftRadius: 'var(--radius-sm)', borderBottomRightRadius: 'var(--radius-sm)' }}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
                  <div style={{ background: 'var(--t-bg-secondary)', borderRadius: 8, padding: '12px 16px', textAlign: 'center' }}>
                    <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--t-text-bright)' }}>{docCount.toLocaleString()}</div>
                    <div style={{ fontSize: 11, color: 'var(--t-text-muted)', marginTop: 2 }}>Documents</div>
                  </div>
                  <div style={{ background: 'var(--t-bg-secondary)', borderRadius: 8, padding: '12px 16px', textAlign: 'center' }}>
                    <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--t-text-bright)' }}>{chunkCount.toLocaleString()}</div>
                    <div style={{ fontSize: 11, color: 'var(--t-text-muted)', marginTop: 2 }}>Chunks</div>
                  </div>
                  <div style={{ background: 'var(--t-bg-secondary)', borderRadius: 8, padding: '12px 16px', textAlign: 'center' }}>
                    <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--t-text-bright)' }}>{ratio}</div>
                    <div style={{ fontSize: 11, color: 'var(--t-text-muted)', marginTop: 2 }}>Chunks / Doc</div>
                  </div>
                </div>
                {docCount === 0 && (
                  <div style={{ marginTop: 12, padding: '8px 12px', borderRadius: 6, background: 'rgba(231,76,60,0.1)', border: '1px solid rgba(231,76,60,0.25)', color: '#e74c3c', fontSize: 12 }}>
                    No documents ingested. Run the KB pipeline to populate this module.
                  </div>
                )}
                {docCount > 0 && chunkCount === 0 && (
                  <div style={{ marginTop: 12, padding: '8px 12px', borderRadius: 6, background: 'rgba(243,156,18,0.1)', border: '1px solid rgba(243,156,18,0.25)', color: '#f39c12', fontSize: 12 }}>
                    Documents exist but no chunks — the chunking pipeline may not have run.
                  </div>
                )}
              </div>
            )}

            {/* AI / Phone Support sub-feature toggles */}
            {(m.slug === 'ai' || m.slug === 'phone_support') && m.enabled && isExpanded && aiFeatures.length > 0 && (
              <div style={{ padding: '12px 16px', background: 'var(--t-panel)', border: '1px solid var(--t-border)', borderTop: 'none', borderBottomLeftRadius: 'var(--radius-sm)', borderBottomRightRadius: 'var(--radius-sm)' }}>
                <div className="feature-grid" style={{ marginTop: 0 }}>
                  {aiFeatures.map((f) => (
                    <div key={f.id} className="feature-card">
                      <div className="feature-info">
                        <div className="feature-name">{f.name}</div>
                        <div className="feature-desc">{f.description}</div>
                      </div>
                      <label className="toggle feature-toggle">
                        <input
                          type="checkbox"
                          checked={f.enabled}
                          onChange={() => toggleFeature(f.id, f.enabled)}
                        />
                        <div className="toggle-track" />
                        <div className="toggle-thumb" />
                      </label>
                    </div>
                  ))}
                </div>
                {m.slug === 'phone_support' && (
                  <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px solid var(--t-border-subtle, rgba(255,255,255,0.06))' }}>
                    <button className="btn btn-ghost btn-sm" style={{ fontSize: 12 }}
                      aria-label="Open Communications settings"
                      onClick={(e) => { e.stopPropagation(); onNavigateTab('phone'); }}>
                      Open Communications →
                    </button>
                  </div>
                )}
              </div>
            )}

            {/* Customer Portal config panel */}
            {m.slug === 'customer_portal' && m.enabled && isExpanded && (
              <div style={{ padding: '12px 16px', background: 'var(--t-panel)', border: '1px solid var(--t-border)', borderTop: 'none', borderBottomLeftRadius: 'var(--radius-sm)', borderBottomRightRadius: 'var(--radius-sm)' }}>
                <div style={{ fontSize: 12, color: 'var(--t-text-muted)', marginBottom: 10 }}>
                  Customize the portal's logo, banner, hero text, and theme colors.
                </div>
                <button
                  onClick={() => onNavigateTab('portal')}
                  className="btn btn-secondary"
                  style={{ fontSize: 12 }}
                >
                  Configure Portal Appearance →
                </button>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );

  // ── KB section (used in Modules tab) ───────────────────────
  const renderKBSection = () => {
    const enabledCount = knowledgeModules.filter(m => m.enabled).length;
    const totalDocs = knowledgeModules.reduce((s, m) => s + ((m as any).doc_count ?? 0), 0);

    const catCounts: Record<string, { total: number; enabled: number }> = {};
    for (const cat of Object.keys(MODULE_CATEGORY_META) as ModuleCategory[]) {
      const inCat = knowledgeModules.filter(m => MODULE_CATEGORIES[m.slug] === cat);
      catCounts[cat] = { total: inCat.length, enabled: inCat.filter(m => m.enabled).length };
    }

    const searchLower = kbSearch.toLowerCase();
    const filteredKB = knowledgeModules.filter(m => {
      if (kbFilter === 'enabled' && !m.enabled) return false;
      if (kbFilter !== 'all' && kbFilter !== 'enabled' && MODULE_CATEGORIES[m.slug] !== kbFilter) return false;
      if (searchLower && !m.name.toLowerCase().includes(searchLower) && !m.description.toLowerCase().includes(searchLower)) return false;
      return true;
    });

    const grouped: Record<string, (KnowledgeModule & { enabled: boolean })[]> = {};
    for (const m of filteredKB) {
      const cat = MODULE_CATEGORIES[m.slug] || 'other';
      if (!grouped[cat]) grouped[cat] = [];
      grouped[cat].push(m);
    }
    const categoryOrder: string[] = ['pos', 'office', 'operations', 'finance', 'other'];

    return (
      <div style={{ marginTop: 24 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--t-text-bright)', margin: 0 }}>
            Knowledge Bases
          </h3>
          <div style={{ display: 'flex', gap: 12, fontSize: 11, color: 'var(--t-text-muted)' }}>
            <span><b style={{ color: 'var(--t-accent)' }}>{enabledCount}</b> / {knowledgeModules.length} enabled</span>
            <span>{totalDocs.toLocaleString()} docs total</span>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
          {([['all', 'All', 'var(--t-text-muted)'], ['enabled', 'Enabled', 'var(--t-accent)']] as const).map(([key, label, color]) => (
            <button
              key={key}
              onClick={() => setKbFilter(key)}
              style={{
                padding: '4px 12px', borderRadius: 20, fontSize: 11, fontWeight: 500,
                border: kbFilter === key ? '1px solid var(--t-accent)' : '1px solid var(--t-border)',
                background: kbFilter === key ? 'var(--t-accent-bg, rgba(var(--accent-rgb), 0.15))' : 'transparent',
                color: kbFilter === key ? 'var(--t-accent)' : color,
                cursor: 'pointer', transition: 'all 0.15s',
              }}
            >
              {label}{key === 'enabled' ? ` (${enabledCount})` : ''}
            </button>
          ))}
          <span style={{ width: 1, height: 16, background: 'var(--t-border)', margin: '0 2px' }} />
          {(Object.keys(MODULE_CATEGORY_META) as ModuleCategory[]).map(cat => (
            <button
              key={cat}
              onClick={() => setKbFilter(kbFilter === cat ? 'all' : cat)}
              style={{
                padding: '4px 12px', borderRadius: 20, fontSize: 11, fontWeight: 500,
                border: kbFilter === cat ? `1px solid ${MODULE_CATEGORY_META[cat].color}` : '1px solid var(--t-border)',
                background: kbFilter === cat ? `${MODULE_CATEGORY_META[cat].color}22` : 'transparent',
                color: kbFilter === cat ? MODULE_CATEGORY_META[cat].color : 'var(--t-text-muted)',
                cursor: 'pointer', transition: 'all 0.15s',
              }}
            >
              {MODULE_CATEGORY_META[cat].label} ({catCounts[cat]?.total || 0})
            </button>
          ))}
          <div style={{ marginLeft: 'auto' }}>
            <input
              type="text"
              placeholder="Search modules..."
              value={kbSearch}
              onChange={e => setKbSearch(e.target.value)}
              className="input"
              style={{ fontSize: 11, padding: '4px 10px', width: 160 }}
            />
          </div>
        </div>

        {categoryOrder.filter(c => grouped[c]?.length).map(cat => {
          const meta = MODULE_CATEGORY_META[cat as ModuleCategory];
          return (
            <div key={cat} style={{ marginBottom: 20 }}>
              {(kbFilter === 'all' || kbFilter === 'enabled') && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                  <span style={{ width: 8, height: 8, borderRadius: '50%', background: meta?.color || 'var(--t-text-muted)', flexShrink: 0 }} />
                  <span style={{ fontSize: 12, fontWeight: 600, color: meta?.color || 'var(--t-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                    {meta?.label || 'Other'}
                  </span>
                  <span style={{ flex: 1, height: 1, background: 'var(--t-border)' }} />
                </div>
              )}
              {renderModuleGrid(grouped[cat])}
            </div>
          );
        })}

        {filteredKB.length === 0 && (
          <div style={{ padding: 24, textAlign: 'center', color: 'var(--t-text-muted)', fontSize: 12 }}>
            No modules match the current filter.
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="tenant-detail">
      {/* Tab bar */}
      <div className="tenant-tabs">
        {DETAIL_TABS.map((t) => (
          <button
            key={t.key}
            className={`tenant-tab${activeTab === t.key ? ' active' : ''}`}
            onClick={() => setActiveTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Overview tab */}
      {activeTab === 'overview' && (
        <div>
          <div className="card" style={{ padding: 20, marginBottom: 16 }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
              <div>
                <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--t-text-bright)', marginBottom: 4 }}>{tenant.name}</div>
                <div style={{ fontSize: 12, color: 'var(--t-text-muted)', marginBottom: 8 }}>
                  {tenant.slug}{(tenant as any).domain ? ` · ${(tenant as any).domain}` : ''}
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                  <span className={`badge ${tenant.is_active ? 'badge-resolved' : 'badge-p3'}`}>
                    {tenant.is_active ? 'Active' : 'Inactive'}
                  </span>
                  {plan && (
                    <span className={`badge ${plan.plan_tier === 'paid' ? 'badge-resolved' : plan.plan_tier === 'trial' ? 'badge-p2' : 'badge-p3'}`}>
                      {plan.plan_tier.charAt(0).toUpperCase() + plan.plan_tier.slice(1)}
                    </span>
                  )}
                </div>
              </div>
              {plan?.plan_expires_at && (
                <div style={{ fontSize: 12, color: 'var(--t-text-muted)', textAlign: 'right' }}>
                  <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 2 }}>Expires</div>
                  {new Date(plan.plan_expires_at).toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })}
                </div>
              )}
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 12 }}>
            <div className="card" style={{ padding: '16px 20px', textAlign: 'center' }}>
              <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--t-accent)' }}>{(tenant as any).user_count || 0}</div>
              <div style={{ fontSize: 11, color: 'var(--t-text-muted)', marginTop: 4 }}>Users</div>
            </div>
            <div className="card" style={{ padding: '16px 20px', textAlign: 'center' }}>
              <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--t-accent)' }}>{tenant.enabled_modules}</div>
              <div style={{ fontSize: 11, color: 'var(--t-text-muted)', marginTop: 4 }}>Modules Enabled</div>
            </div>
            <div className="card" style={{ padding: '16px 20px', textAlign: 'center' }}>
              <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--t-accent)' }}>
                {knowledgeModules.filter(m => m.enabled).length}
              </div>
              <div style={{ fontSize: 11, color: 'var(--t-text-muted)', marginTop: 4 }}>KB Modules</div>
            </div>
            <div className="card" style={{ padding: '16px 20px', textAlign: 'center' }}>
              <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--t-accent)' }}>
                {knowledgeModules.reduce((s, m) => s + ((m as any).doc_count ?? 0), 0).toLocaleString()}
              </div>
              <div style={{ fontSize: 11, color: 'var(--t-text-muted)', marginTop: 4 }}>Total Docs</div>
            </div>
          </div>

          {tenant.created_at && (
            <div style={{ marginTop: 12, fontSize: 11, color: 'var(--t-text-muted)' }}>
              Tenant created {new Date(tenant.created_at).toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })}
            </div>
          )}
        </div>
      )}

      {/* Modules tab */}
      {activeTab === 'modules' && (
        <div>
          {featureModules.length > 0 && (
            <div style={{ marginBottom: 24 }}>
              <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--t-text-bright)', marginBottom: 16 }}>
                Feature Modules
              </h3>
              {renderModuleGrid(featureModules)}
            </div>
          )}
          {knowledgeModules.length > 0 && renderKBSection()}
        </div>
      )}

      {/* Plan tab */}
      {activeTab === 'plan' && (
        <div>
          <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--t-text-bright)', marginBottom: 16 }}>
            Plan &amp; Billing
          </h3>
          {plan ? (
            <div className="card" style={{ padding: 20 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
                <span className={`badge ${plan.plan_tier === 'paid' ? 'badge-resolved' : plan.plan_tier === 'trial' ? 'badge-p2' : 'badge-p3'}`}
                      style={{ fontSize: 13, padding: '4px 12px' }}>
                  {plan.plan_tier.charAt(0).toUpperCase() + plan.plan_tier.slice(1)}
                </span>
                {plan.plan_expires_at && (
                  <span style={{ fontSize: 12, color: 'var(--t-text-muted)' }}>
                    Expires: {new Date(plan.plan_expires_at).toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })}
                  </span>
                )}
                {!plan.plan_expires_at && plan.plan_tier !== 'free' && (
                  <span style={{ fontSize: 12, color: 'var(--t-text-muted)' }}>No expiration set</span>
                )}
              </div>

              {plan.plan_extended_by_name && (
                <div style={{ fontSize: 11, color: 'var(--t-text-dim)', marginBottom: 16 }}>
                  Last extended by {plan.plan_extended_by_name}
                  {plan.plan_extended_at && ` on ${new Date(plan.plan_extended_at).toLocaleDateString()}`}
                </div>
              )}

              <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
                <div className="form-group" style={{ margin: 0 }}>
                  <label className="form-label" style={{ fontSize: 11, marginBottom: 4 }}>Tier</label>
                  <select
                    className="form-input form-select"
                    value={planTier}
                    onChange={(e) => setPlanTier(e.target.value)}
                    style={{ width: 120 }}
                  >
                    <option value="free">Free</option>
                    <option value="trial">Trial</option>
                    <option value="starter">Starter ($50/seat)</option>
                    <option value="pro">Pro ($100/seat)</option>
                    <option value="business">Business ($150/seat)</option>
                    <option value="enterprise">Enterprise BYOK ($100/seat)</option>
                  </select>
                </div>

                <div className="form-group" style={{ margin: 0 }}>
                  <label className="form-label" style={{ fontSize: 11, marginBottom: 4 }}>Extend by (days)</label>
                  <input
                    className="form-input"
                    type="number"
                    min={1}
                    max={365}
                    value={extendDays}
                    onChange={(e) => setExtendDays(e.target.value)}
                    style={{ width: 80 }}
                  />
                </div>

                <button
                  className="btn btn-primary btn-sm"
                  disabled={planSaving}
                  onClick={onSavePlan}
                >
                  {planSaving ? 'Saving...' : 'Update Plan'}
                </button>

                {planSaved && <span style={{ fontSize: 12, color: 'var(--t-success)' }}>Saved!</span>}
              </div>
            </div>
          ) : (
            <div className="card" style={{ padding: 24, textAlign: 'center', color: 'var(--t-text-muted)', fontSize: 12 }}>
              Plan data unavailable.
            </div>
          )}
        </div>
      )}

      {/* Settings tab */}
      {activeTab === 'settings' && (
        <div>
          <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--t-text-bright)', marginBottom: 16 }}>
            Settings
          </h3>
          <div className="admin-grid">
            {SETTINGS_CARDS.map((card) => (
              <div
                key={card.tab}
                className="module-card"
                style={{ cursor: 'pointer' }}
                onClick={() => onNavigateTab(card.tab)}
              >
                <div className="module-card-header">
                  <div className="module-card-info">
                    <div className="module-card-icon">{card.icon}</div>
                    <div>
                      <div className="module-card-name">{card.title}</div>
                      <div className="module-card-desc">{card.description}</div>
                    </div>
                  </div>
                  <span style={{ fontSize: 14, color: 'var(--t-text-muted)', flexShrink: 0 }}>→</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}


/* ============================================================
   TENANT LIST
   ============================================================ */
function TenantList({ onNavigateTab }: { onNavigateTab: (tab: Tab) => void }) {
  const [tenants, setTenants] = useState<Tenant[]>(_cachedTenants);
  const [selectedTenant, setSelectedTenant] = useState<number | null>(null);
  const [modules, setModules] = useState<(KnowledgeModule & { enabled: boolean })[]>([]);
  const [aiFeatures, setAiFeatures] = useState<ModuleFeature[]>([]);
  const [expandedModule, setExpandedModule] = useState<number | null>(null);

  // Plan management state
  const [plan, setPlan] = useState<PlanData | null>(null);
  const [planTier, setPlanTier] = useState('free');
  const [extendDays, setExtendDays] = useState('30');
  const [planSaving, setPlanSaving] = useState(false);
  const [planSaved, setPlanSaved] = useState(false);

  useEffect(() => {
    api.listTenants().then((t) => { _cachedTenants = t; setTenants(t); }).catch(() => {});
  }, []);

  useEffect(() => {
    if (selectedTenant) {
      api.getTenantModules(selectedTenant).then(setModules).catch(() => {});
      api.getTenantPlan(selectedTenant).then((p: any) => {
        setPlan(p);
        setPlanTier(p.plan_tier || 'free');
      }).catch(() => setPlan(null));
    }
  }, [selectedTenant]);

  // Load sub-features when a module is expanded
  useEffect(() => {
    if (selectedTenant && expandedModule) {
      api.getModuleFeatures(selectedTenant, expandedModule).then(setAiFeatures).catch(() => {});
    }
  }, [selectedTenant, expandedModule]);

  // Eagerly load phone_service feature so the Phone Support card shows correct state
  useEffect(() => {
    if (!selectedTenant || !modules.length) return;
    const aiModule = modules.find((m) => m.slug === 'ai');
    if (!aiModule) return;
    api.getModuleFeatures(selectedTenant, aiModule.id).catch(() => {});
  }, [selectedTenant, modules]);

  const toggleModule = async (moduleId: number, enabled: boolean) => {
    if (!selectedTenant) return;
    if (enabled) {
      await api.disableModule(selectedTenant, moduleId);
      if (expandedModule === moduleId) setExpandedModule(null);
    } else {
      await api.enableModule(selectedTenant, moduleId);
    }
    api.getTenantModules(selectedTenant).then(setModules).catch(() => {});
  };

  const toggleFeature = async (featureId: number, enabled: boolean) => {
    if (!selectedTenant) return;
    if (enabled) {
      await api.disableFeature(selectedTenant, featureId);
    } else {
      await api.enableFeature(selectedTenant, featureId);
    }
    if (expandedModule) {
      api.getModuleFeatures(selectedTenant, expandedModule).then(setAiFeatures).catch(() => {});
    }
  };

  const handleSavePlan = async () => {
    if (!selectedTenant) return;
    setPlanSaving(true);
    setPlanSaved(false);
    try {
      await api.updateTenantPlan(selectedTenant, {
        plan_tier: planTier,
        extend_days: parseInt(extendDays) || undefined,
      });
      const updated = await api.getTenantPlan(selectedTenant) as any;
      setPlan(updated);
      setPlanTier(updated.plan_tier || 'free');
      setPlanSaved(true);
      setTimeout(() => setPlanSaved(false), 3000);
    } catch (err) {
      console.error('Failed to update plan', err);
    } finally {
      setPlanSaving(false);
    }
  };

  const selectedTenantData = tenants.find((t) => t.id === selectedTenant) ?? null;

  // No tenant selected → show full-width grid
  if (!selectedTenant || !selectedTenantData) {
    return (
      <div>
        <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--t-text-bright)', marginBottom: 16 }}>
          Tenants
        </h3>
        <div className="admin-grid">
          {tenants.map((t) => (
            <div
              key={t.id}
              className="card"
              style={{ cursor: 'pointer' }}
              onClick={() => setSelectedTenant(t.id)}
            >
              <div style={{ fontWeight: 600, color: 'var(--t-text-bright)' }}>{t.name}</div>
              <div style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>
                {t.slug}{(t as any).domain ? ` · ${(t as any).domain}` : ''} · {t.enabled_modules} modules · {(t as any).user_count || 0} users
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  // Tenant selected → list + detail layout
  return (
    <div className="tenant-manager">
      {/* Narrow tenant list sidebar */}
      <div className="tenant-sidebar">
        <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--t-text-muted)', padding: '0 10px 8px' }}>
          Tenants
        </div>
        {tenants.map((t) => (
          <button
            key={t.id}
            className={`admin-sidenav-item${selectedTenant === t.id ? ' active' : ''}`}
            onClick={() => setSelectedTenant(t.id)}
          >
            <div style={{ fontWeight: 600, fontSize: 13, color: 'inherit' }}>{t.name}</div>
            <div style={{ fontSize: 10, color: 'var(--t-text-muted)', marginTop: 1 }}>{t.slug}</div>
          </button>
        ))}
        <button
          className="btn btn-ghost btn-sm"
          style={{ margin: '8px 10px 0', fontSize: 11 }}
          onClick={() => setSelectedTenant(null)}
        >
          ← All Tenants
        </button>
      </div>

      {/* Detail panel */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <TenantDetail
          tenant={selectedTenantData}
          modules={modules}
          aiFeatures={aiFeatures}
          expandedModule={expandedModule}
          setExpandedModule={setExpandedModule}
          toggleModule={toggleModule}
          toggleFeature={toggleFeature}
          plan={plan}
          planTier={planTier}
          setPlanTier={setPlanTier}
          extendDays={extendDays}
          setExtendDays={setExtendDays}
          planSaving={planSaving}
          planSaved={planSaved}
          onSavePlan={handleSavePlan}
          onNavigateTab={onNavigateTab}
        />
      </div>
    </div>
  );
}


/* ============================================================
   REPORTS PLACEHOLDER
   ============================================================ */
function ReportsPlaceholder() {
  return (
    <div style={{ maxWidth: 560 }}>
      <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--t-text-bright)', margin: '0 0 16px' }}>Reports</h3>
      <div className="card" style={{ padding: 32, textAlign: 'center' }}>
        <div style={{ marginBottom: 12 }}>
          <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--t-text-muted)', background: 'var(--t-panel-alt)', padding: '3px 10px', borderRadius: 4 }}>
            Coming soon
          </span>
        </div>
        <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--t-text-bright)', marginBottom: 8 }}>
          Analytics &amp; Reporting
        </div>
        <div style={{ fontSize: 13, color: 'var(--t-text-muted)', lineHeight: 1.6 }}>
          Ticket volume, category breakdown, location &times; category insights, and agent performance — across all your locations and teams.
        </div>
      </div>
    </div>
  );
}

/* ============================================================
   USERS
   ============================================================ */
let _cachedUsers: AdminUser[] = [];

function UserList() {
  const [users, setUsers] = useState<AdminUser[]>(_cachedUsers);
  const [showInvite, setShowInvite] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const isSuperAdmin = useAuthStore((s) => s.isSuperAdmin);
  const fileRef = useRef<HTMLInputElement>(null);
  const [importStatus, setImportStatus] = useState<string | null>(null);

  // Invite form state
  const [inviteData, setInviteData] = useState({ first_name: '', last_name: '', email: '', phone: '', role: 'end_user', expires_at: '' });
  const [inviteError, setInviteError] = useState<string | null>(null);

  // Edit form state
  const [editingUserId, setEditingUserId] = useState<number | null>(null);
  const [editData, setEditData] = useState({ first_name: '', last_name: '', phone: '', role: 'end_user', tenant_id: '' });
  const [editError, setEditError] = useState<string | null>(null);
  const [editLoading, setEditLoading] = useState(false);

  // User location assignment state
  const [editUserLocations, setEditUserLocations] = useState<number[]>([]);
  const [allLocations, setAllLocations] = useState<any[]>([]);
  const [locationFilter, setLocationFilter] = useState('');

  // User group + team assignment state
  const [editUserGroups, setEditUserGroups] = useState<number[]>([]);
  const [editUserTeams, setEditUserTeams] = useState<number[]>([]);
  const [allGroups, setAllGroups] = useState<any[]>([]);
  const [allTeams, setAllTeams] = useState<any[]>([]);

  // Per-user permission overrides
  const [editUserOverrides, setEditUserOverrides] = useState<Record<number, { granted: boolean; reason: string } | null>>({});
  const [allPermissions, setAllPermissions] = useState<{ id: number; slug: string; label: string; category: string }[]>([]);
  const [showOverrides, setShowOverrides] = useState(false);

  // Tenant filter state (super_admin only)
  const [tenants, setTenants] = useState<any[]>([]);
  const [tenantFilter, setTenantFilter] = useState<number | 'all'>('all');
  // Role filter
  const [roleFilter, setRoleFilter] = useState<string>('all');

  const refreshUsers = () => {
    api.listUsers().then((u) => { _cachedUsers = u; setUsers(u); }).catch(() => {});
  };

  useEffect(() => {
    refreshUsers();
    if (isSuperAdmin()) {
      api.listTenants().then(setTenants).catch(() => {});
    }
  }, []);

  const handleInvite = async () => {
    setInviteError(null);
    if (!inviteData.email) { setInviteError('Email is required'); return; }
    try {
      const tenantId = useAuthStore.getState().user?.tenant_id;
      await api.createUser({ ...inviteData, tenant_id: tenantId, expires_at: inviteData.expires_at || undefined });
      setShowInvite(false);
      setInviteData({ first_name: '', last_name: '', email: '', phone: '', role: 'end_user', expires_at: '' });
      refreshUsers();
    } catch (err: any) {
      setInviteError(err.message);
    }
  };

  const handleRevoke = async (userId: number) => {
    await api.updateUser(userId, { invite_status: 'revoked', is_active: false });
    refreshUsers();
  };

  const handleResend = async (userId: number) => {
    try {
      await api.resendInvite(userId);
      refreshUsers();
    } catch (err: any) {
      console.error('Failed to resend invite', err);
    }
  };

  const handleBulkImport = async (file: File) => {
    try {
      setImportStatus('Importing...');
      const tenantId = useAuthStore.getState().user?.tenant_id;
      const result = await api.bulkImportUsers(file, tenantId || undefined);
      setImportStatus(`${result.created} created, ${result.skipped} skipped${result.errors.length ? `, ${result.errors.length} errors` : ''}`);
      refreshUsers();
      setTimeout(() => { setImportStatus(null); setShowImport(false); }, 4000);
    } catch (err: any) {
      setImportStatus(`Error: ${err.message}`);
    }
  };

  const handleEditOpen = (u: AdminUser) => {
    setEditingUserId(u.id);
    setEditData({
      first_name: u.first_name || '',
      last_name: u.last_name || '',
      phone: u.phone || '',
      role: u.role,
      tenant_id: u.tenant_id != null ? String(u.tenant_id) : '',
    });
    setEditError(null);
    setEditUserLocations([]);
    setLocationFilter('');
    setEditUserGroups([]);
    setEditUserTeams([]);
    // Load locations, groups, teams for assignment
    if (allLocations.length === 0) {
      api.listLocations().then(setAllLocations).catch(() => {});
    }
    if (allGroups.length === 0) {
      api.listGroups().then(setAllGroups).catch(() => {});
    }
    if (allTeams.length === 0) {
      api.listTeams().then(setAllTeams).catch(() => {});
    }
    api.getUserLocations(u.id).then((rows) => setEditUserLocations(rows.map((r) => r.location_id))).catch(() => {});
    api.getUserGroups(u.id).then((rows) => setEditUserGroups(rows.map((r) => r.id))).catch(() => {});
    api.getUserTeams(u.id).then((rows) => setEditUserTeams(rows.map((r) => r.id))).catch(() => {});
    // Load permission overrides (keyed by slug for simplicity)
    setEditUserOverrides({});
    setShowOverrides(false);
    const permPromise = allPermissions.length === 0
      ? api.getPermissionMatrix().then((m) => { const p = m.permissions || []; setAllPermissions(p); return p; })
      : Promise.resolve(allPermissions);
    permPromise.then(() => {
      api.getUserPermissions(u.id).then((res) => {
        const map: Record<string, { granted: boolean; reason: string }> = {};
        for (const ov of res.overrides || []) {
          map[ov.slug] = { granted: ov.granted, reason: ov.reason || '' };
        }
        setEditUserOverrides(map);
      }).catch(() => {});
    }).catch(() => {});
  };

  const handleEditSave = async (userId: number) => {
    setEditError(null);
    setEditLoading(true);
    try {
      const payload: Record<string, any> = {
        first_name: editData.first_name,
        last_name: editData.last_name,
        phone: editData.phone,
        role: editData.role,
      };
      if (isSuperAdmin() && editData.tenant_id !== '') {
        payload.tenant_id = Number(editData.tenant_id);
      }
      await api.updateUser(userId, payload);
      // Build overrides array from state
      const overrideEntries = Object.entries(editUserOverrides)
        .filter(([, v]) => v !== null)
        .map(([slug, v]) => {
          const perm = allPermissions.find((p) => p.slug === slug);
          return perm ? { permission_id: perm.id, granted: v!.granted, reason: v!.reason || '' } : null;
        })
        .filter(Boolean) as { permission_id: number; granted: boolean; reason: string }[];
      await Promise.all([
        api.setUserLocations(userId, editUserLocations),
        api.setUserGroups(userId, editUserGroups),
        api.setUserTeams(userId, editUserTeams),
        api.setUserPermissionOverrides(userId, overrideEntries),
      ]);
      setEditingUserId(null);
      refreshUsers();
    } catch (err: any) {
      setEditError(err.message);
    } finally {
      setEditLoading(false);
    }
  };

  const buildLocationPath = (locs: any[], locId: number): string => {
    const map = new Map(locs.map((l) => [l.id, l]));
    const parts: string[] = [];
    let cur = map.get(locId);
    while (cur) {
      parts.unshift(cur.name);
      cur = cur.parent_id != null ? map.get(cur.parent_id) : undefined;
    }
    return parts.join(' > ');
  };

  const statusBadge = (status: string) => {
    const map: Record<string, string> = { active: 'badge-resolved', invited: 'badge-p2', expired: 'badge-closed_not_resolved', revoked: 'badge-closed_not_resolved' };
    return map[status] || 'badge-p3';
  };

  const currentUserId = useAuthStore.getState().user?.id;
  const displayedUsers = users
    .filter((u) => tenantFilter === 'all' || u.tenant_id === tenantFilter)
    .filter((u) => roleFilter === 'all' || u.role === roleFilter);

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--t-text-bright)', margin: 0 }}>
          Users
        </h3>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <label style={{ fontSize: 12, color: 'var(--t-text-muted)', whiteSpace: 'nowrap' }}>Role</label>
            <select
              className="input"
              style={{ fontSize: 12, padding: '4px 8px', height: 30 }}
              value={roleFilter}
              onChange={(e) => setRoleFilter(e.target.value)}
              aria-label="Filter by role"
            >
              <option value="all">All Roles</option>
              <option value="end_user">End User</option>
              <option value="agent">Agent</option>
              <option value="tenant_admin">Tenant Admin</option>
              {isSuperAdmin() && <option value="super_admin">Platform Admin</option>}
            </select>
          </div>
          {isSuperAdmin() && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <label style={{ fontSize: 12, color: 'var(--t-text-muted)', whiteSpace: 'nowrap' }}>Tenant</label>
              <select
                className="input"
                style={{ fontSize: 12, padding: '4px 8px', height: 30 }}
                value={tenantFilter === 'all' ? 'all' : String(tenantFilter)}
                onChange={(e) => setTenantFilter(e.target.value === 'all' ? 'all' : Number(e.target.value))}
                aria-label="Filter by tenant"
              >
                <option value="all">All Tenants</option>
                {tenants.map((t) => (
                  <option key={t.id} value={t.id}>{t.name}</option>
                ))}
              </select>
            </div>
          )}
          <button className="btn btn-sm btn-ghost" onClick={() => { const tid = useAuthStore.getState().user?.tenant_id; api.exportUsers(tid || undefined); }}>
            Export CSV
          </button>
          <button className={`btn btn-sm ${showImport ? 'btn-primary' : 'btn-ghost'}`} onClick={() => { setShowImport(!showImport); setShowInvite(false); }}>
            Import CSV
          </button>
          <button className="btn btn-sm btn-primary" onClick={() => { setShowInvite(!showInvite); setShowImport(false); }}>
            + Invite User
          </button>
        </div>
      </div>

      {showImport && (
        <div className="card" style={{ padding: 20, marginBottom: 16 }}>
          <p style={{ fontSize: 12, color: 'var(--t-text-muted)', marginBottom: 12 }}>
            CSV columns: first_name, last_name, email, phone, role (tenant_admin / agent / end_user), groups (comma-separated), teams (comma-separated)
          </p>
          <input
            ref={fileRef}
            type="file"
            accept=".csv"
            style={{ display: 'none' }}
            onChange={(e) => { const f = e.target.files?.[0]; if (f) handleBulkImport(f); }}
          />
          <button className="btn btn-primary btn-sm" onClick={() => fileRef.current?.click()}>
            Choose File
          </button>
          {importStatus && (
            <span style={{ marginLeft: 12, fontSize: 12, color: importStatus.startsWith('Error') ? 'var(--t-status-urgent)' : 'var(--t-accent)' }}>
              {importStatus}
            </span>
          )}
        </div>
      )}

      {showInvite && (
        <div className="card" style={{ padding: 20, marginBottom: 16 }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
            <input className="input" placeholder="First Name" value={inviteData.first_name} onChange={(e) => setInviteData({ ...inviteData, first_name: e.target.value })} />
            <input className="input" placeholder="Last Name" value={inviteData.last_name} onChange={(e) => setInviteData({ ...inviteData, last_name: e.target.value })} />
            <input className="input" placeholder="Email *" type="email" value={inviteData.email} onChange={(e) => setInviteData({ ...inviteData, email: e.target.value })} />
            <input className="input" placeholder="Phone" value={inviteData.phone} onChange={(e) => setInviteData({ ...inviteData, phone: e.target.value })} />
            <select className="input" value={inviteData.role} onChange={(e) => setInviteData({ ...inviteData, role: e.target.value })}>
              <option value="end_user">End User</option>
              <option value="agent">Agent</option>
              <option value="tenant_admin">Tenant Admin</option>
              {isSuperAdmin() && <option value="super_admin">Platform Admin</option>}
            </select>
            <input className="input" type="date" placeholder="Expires (optional)" value={inviteData.expires_at} onChange={(e) => setInviteData({ ...inviteData, expires_at: e.target.value })} />
          </div>
          {inviteError && <div style={{ fontSize: 12, color: 'var(--t-status-urgent)', marginBottom: 8 }}>{inviteError}</div>}
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn-primary btn-sm" onClick={handleInvite}>Send Invite</button>
            <button className="btn btn-ghost btn-sm" onClick={() => setShowInvite(false)}>Cancel</button>
          </div>
        </div>
      )}

      <div className="user-table">
        <div className="user-table-header">
          <span style={{ flex: 2 }}>Name</span>
          <span style={{ flex: 2 }}>Email</span>
          <span style={{ flex: 1 }}>Phone</span>
          <span style={{ flex: 1 }}>Role</span>
          <span style={{ flex: 1 }}>Status</span>
          <span style={{ flex: 1 }}>Tenant</span>
          <span style={{ width: 152, textAlign: 'right' }}>Actions</span>
        </div>
        {displayedUsers.map((u) => (
          <div key={u.id}>
            <div className="user-table-row">
              <span style={{ flex: 2, fontWeight: 500 }}>{u.first_name || u.last_name ? `${u.first_name || ''} ${u.last_name || ''}`.trim() : u.name || u.email}</span>
              <span style={{ flex: 2, color: 'var(--t-text-muted)', fontSize: 12 }}>{u.email}</span>
              <span style={{ flex: 1, color: 'var(--t-text-muted)', fontSize: 12 }}>{u.phone || '—'}</span>
              <span style={{ flex: 1 }}><span className="badge badge-p3">{({ super_admin: 'Platform Admin', tenant_admin: 'Tenant Admin', agent: 'Agent', end_user: 'End User' } as Record<string, string>)[u.role] ?? u.role}</span></span>
              <span style={{ flex: 1 }}><span className={`badge ${statusBadge(u.invite_status)}`}>{u.invite_status}</span></span>
              <span style={{ flex: 1, color: 'var(--t-text-muted)', fontSize: 12 }}>{u.tenant_name || '—'}</span>
              <span style={{ width: 152, textAlign: 'right', display: 'flex', gap: 4, justifyContent: 'flex-end' }}>
                {u.id !== currentUserId && (
                  <button
                    className="btn btn-ghost btn-sm"
                    style={{ fontSize: 11, padding: '2px 8px' }}
                    aria-label={`Edit ${u.email}`}
                    onClick={() => {
                      if (editingUserId === u.id) {
                        setEditingUserId(null);
                      } else {
                        handleEditOpen(u);
                        setShowInvite(false);
                      }
                    }}
                  >
                    {editingUserId === u.id ? 'Close' : 'Edit'}
                  </button>
                )}
                {u.invite_status === 'invited' && (
                  <button className="btn btn-ghost btn-sm" style={{ fontSize: 11, padding: '2px 8px', color: 'var(--t-accent)' }} onClick={() => handleResend(u.id)}>
                    Resend
                  </button>
                )}
                {u.invite_status !== 'revoked' && u.is_active && (
                  <button className="btn btn-ghost btn-sm" style={{ fontSize: 11, padding: '2px 8px' }} onClick={() => handleRevoke(u.id)}>
                    Revoke
                  </button>
                )}
              </span>
            </div>

            {editingUserId === u.id && (
              <div className="card" style={{ margin: '0 0 4px 0', padding: 16, borderTop: '1px solid var(--t-border)' }}>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
                  <input
                    className="input"
                    placeholder="First Name"
                    value={editData.first_name}
                    onChange={(e) => setEditData({ ...editData, first_name: e.target.value })}
                    aria-label="First name"
                  />
                  <input
                    className="input"
                    placeholder="Last Name"
                    value={editData.last_name}
                    onChange={(e) => setEditData({ ...editData, last_name: e.target.value })}
                    aria-label="Last name"
                  />
                  <input
                    className="input"
                    placeholder="Phone"
                    value={editData.phone}
                    onChange={(e) => setEditData({ ...editData, phone: e.target.value })}
                    aria-label="Phone"
                  />
                  <select
                    className="input"
                    value={editData.role}
                    onChange={(e) => setEditData({ ...editData, role: e.target.value })}
                    aria-label="Role"
                  >
                    <option value="end_user">End User</option>
                    <option value="agent">Agent</option>
                    <option value="tenant_admin">Tenant Admin</option>
                    {isSuperAdmin() && <option value="super_admin">Platform Admin</option>}
                  </select>
                  {isSuperAdmin() && (
                    <select
                      className="input"
                      value={editData.tenant_id}
                      onChange={(e) => setEditData({ ...editData, tenant_id: e.target.value })}
                      aria-label="Tenant"
                    >
                      <option value="">— No Tenant —</option>
                      {tenants.map((t) => (
                        <option key={t.id} value={t.id}>{t.name}</option>
                      ))}
                    </select>
                  )}
                </div>
                {/* Groups + Teams side by side */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 12 }}>
                  {/* Access Control Groups */}
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--t-text-dim)', marginBottom: 6 }}>Access Control Groups</div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, maxHeight: 120, overflowY: 'auto' }}>
                      {allGroups.filter((g: any) => g.is_active !== false).map((g: any) => {
                        const checked = editUserGroups.includes(g.id);
                        return (
                          <label key={g.id} style={{
                            display: 'flex', alignItems: 'center', gap: 6,
                            padding: '4px 10px', borderRadius: 6, cursor: 'pointer', fontSize: 12,
                            background: checked ? 'var(--t-accent-muted, rgba(59,130,246,0.12))' : 'var(--t-panel-alt)',
                            color: checked ? 'var(--t-text-bright)' : 'var(--t-text-dim)',
                            border: checked ? '1px solid var(--t-accent, #3b82f6)' : '1px solid transparent',
                          }}>
                            <input type="checkbox" checked={checked} onChange={() => setEditUserGroups(checked ? editUserGroups.filter((id) => id !== g.id) : [...editUserGroups, g.id])} style={{ display: 'none' }} />
                            {g.name}
                            {g.is_default && <span style={{ fontSize: 9, opacity: 0.5 }}>DEFAULT</span>}
                          </label>
                        );
                      })}
                      {allGroups.filter((g: any) => g.is_active !== false).length === 0 && (
                        <span style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>No groups available</span>
                      )}
                    </div>
                  </div>
                  {/* Teams */}
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--t-text-dim)', marginBottom: 6 }}>Teams</div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, maxHeight: 120, overflowY: 'auto' }}>
                      {allTeams.filter((t: any) => t.is_active !== false).map((t: any) => {
                        const checked = editUserTeams.includes(t.id);
                        return (
                          <label key={t.id} style={{
                            display: 'flex', alignItems: 'center', gap: 6,
                            padding: '4px 10px', borderRadius: 6, cursor: 'pointer', fontSize: 12,
                            background: checked ? 'var(--t-accent-muted, rgba(59,130,246,0.12))' : 'var(--t-panel-alt)',
                            color: checked ? 'var(--t-text-bright)' : 'var(--t-text-dim)',
                            border: checked ? '1px solid var(--t-accent, #3b82f6)' : '1px solid transparent',
                          }}>
                            <input type="checkbox" checked={checked} onChange={() => setEditUserTeams(checked ? editUserTeams.filter((id) => id !== t.id) : [...editUserTeams, t.id])} style={{ display: 'none' }} />
                            {t.name}
                          </label>
                        );
                      })}
                      {allTeams.filter((t: any) => t.is_active !== false).length === 0 && (
                        <span style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>No teams available</span>
                      )}
                    </div>
                  </div>
                </div>

                {allLocations.length > 0 && (() => {
                  const getDescendantIds = (id: number): number[] => {
                    const children = allLocations.filter((l: any) => l.parent_id === id);
                    return children.flatMap((c: any) => [c.id, ...getDescendantIds(c.id)]);
                  };
                  const toggleWithDescendants = (id: number, checked: boolean) => {
                    const affected = [id, ...getDescendantIds(id)];
                    setEditUserLocations(checked
                      ? editUserLocations.filter((x) => !affected.includes(x))
                      : [...new Set([...editUserLocations, ...affected])]);
                  };
                  const renderTree = (parentId: number | null, depth: number): React.ReactNode => {
                    const children = allLocations
                      .filter((l: any) => l.parent_id === parentId)
                      .sort((a: any, b: any) => (a.sort_order ?? 0) - (b.sort_order ?? 0) || a.name.localeCompare(b.name));
                    return children.map((l: any) => {
                      const checked = editUserLocations.includes(l.id);
                      return (
                        <div key={l.id}>
                          <label style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '3px 10px', paddingLeft: 10 + depth * 14, cursor: 'pointer', fontSize: 12, color: checked ? 'var(--t-text-bright)' : 'var(--t-text-dim)' }}>
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={() => toggleWithDescendants(l.id, checked)}
                              style={{ flexShrink: 0 }}
                            />
                            <span style={{ fontWeight: depth === 0 ? 600 : undefined }}>{l.name}</span>
                          </label>
                          {renderTree(l.id, depth + 1)}
                        </div>
                      );
                    });
                  };

                  const filtered = locationFilter
                    ? allLocations.filter((l: any) => buildLocationPath(allLocations, l.id).toLowerCase().includes(locationFilter.toLowerCase()))
                    : null;

                  return (
                    <div style={{ marginBottom: 12 }}>
                      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--t-text-dim)', marginBottom: 6 }}>Assigned Locations</div>
                      <input
                        className="input"
                        placeholder="Filter locations…"
                        value={locationFilter}
                        onChange={(e) => setLocationFilter(e.target.value)}
                        style={{ marginBottom: 6, fontSize: 12, padding: '4px 8px' }}
                      />
                      <div style={{ maxHeight: 200, overflowY: 'auto', border: '1px solid var(--t-border)', borderRadius: 6, padding: '4px 0' }}>
                        {filtered ? (
                          filtered.length === 0 ? (
                            <div style={{ padding: '8px 10px', fontSize: 12, color: 'var(--t-text-muted)' }}>No locations match</div>
                          ) : filtered.map((l: any) => {
                            const checked = editUserLocations.includes(l.id);
                            return (
                              <label key={l.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 10px', cursor: 'pointer', fontSize: 12, color: checked ? 'var(--t-text-bright)' : 'var(--t-text-dim)' }}>
                                <input
                                  type="checkbox"
                                  checked={checked}
                                  onChange={() => setEditUserLocations(checked ? editUserLocations.filter((id) => id !== l.id) : [...editUserLocations, l.id])}
                                  style={{ flexShrink: 0 }}
                                />
                                <span>{buildLocationPath(allLocations, l.id)}</span>
                              </label>
                            );
                          })
                        ) : renderTree(null, 0)}
                      </div>
                    </div>
                  );
                })()}
                {/* Permission Overrides */}
                <div style={{ marginBottom: 12 }}>
                  <button
                    className="btn btn-ghost btn-sm"
                    style={{ fontSize: 12, padding: '4px 10px', marginBottom: 6 }}
                    onClick={() => setShowOverrides(!showOverrides)}
                  >
                    {showOverrides ? '▾' : '▸'} Permission Overrides
                    {Object.values(editUserOverrides).filter(Boolean).length > 0 && (
                      <span className="badge badge-medium" style={{ marginLeft: 6, fontSize: 10 }}>
                        {Object.values(editUserOverrides).filter(Boolean).length}
                      </span>
                    )}
                  </button>
                  {showOverrides && allPermissions.length > 0 && (
                    <div style={{ border: '1px solid var(--t-border)', borderRadius: 6, padding: '8px 0', maxHeight: 260, overflowY: 'auto' }}>
                      <div style={{ padding: '0 10px 6px', fontSize: 10, color: 'var(--t-text-muted)', borderBottom: '1px solid var(--t-border)', marginBottom: 4 }}>
                        Grant or deny individual permissions. Overrides take priority over group permissions.
                      </div>
                      {(() => {
                        const categories = [...new Set(allPermissions.map((p) => p.category || 'General'))];
                        return categories.map((cat) => (
                          <div key={cat}>
                            <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--t-text-dim)', padding: '6px 10px 2px', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{cat}</div>
                            {allPermissions.filter((p) => (p.category || 'General') === cat).map((p) => {
                              const ov = (editUserOverrides as any)[p.slug] as { granted: boolean; reason: string } | null | undefined;
                              const state = ov ? (ov.granted ? 'grant' : 'deny') : 'inherit';
                              return (
                                <div key={p.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '3px 10px', fontSize: 12 }}>
                                  <select
                                    style={{
                                      width: 80, fontSize: 11, padding: '2px 4px', borderRadius: 4,
                                      background: state === 'grant' ? 'rgba(34,197,94,0.15)' : state === 'deny' ? 'rgba(239,68,68,0.15)' : 'var(--t-panel-alt)',
                                      color: state === 'grant' ? 'var(--t-status-ok, #22c55e)' : state === 'deny' ? 'var(--t-status-urgent, #ef4444)' : 'var(--t-text-dim)',
                                      border: '1px solid var(--t-border)',
                                    }}
                                    value={state}
                                    onChange={(e) => {
                                      const val = e.target.value;
                                      setEditUserOverrides((prev) => {
                                        const next = { ...prev };
                                        if (val === 'inherit') {
                                          delete (next as any)[p.slug];
                                        } else {
                                          (next as any)[p.slug] = { granted: val === 'grant', reason: (ov?.reason) || '' };
                                        }
                                        return next;
                                      });
                                    }}
                                  >
                                    <option value="inherit">Inherit</option>
                                    <option value="grant">Grant</option>
                                    <option value="deny">Deny</option>
                                  </select>
                                  <span style={{ flex: 1, color: 'var(--t-text-bright)' }}>{p.label || p.slug}</span>
                                </div>
                              );
                            })}
                          </div>
                        ));
                      })()}
                    </div>
                  )}
                </div>

                {editError && (
                  <div style={{ fontSize: 12, color: 'var(--t-status-urgent)', marginBottom: 8 }}>{editError}</div>
                )}
                <div style={{ display: 'flex', gap: 8 }}>
                  <button
                    className="btn btn-primary btn-sm"
                    onClick={() => handleEditSave(u.id)}
                    disabled={editLoading}
                    aria-label="Save user changes"
                  >
                    {editLoading ? 'Saving…' : 'Save'}
                  </button>
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={() => { setEditingUserId(null); setEditError(null); }}
                    aria-label="Cancel edit"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        ))}
        {displayedUsers.length === 0 && (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--t-text-muted)', fontSize: 13 }}>
            <div style={{ marginBottom: 8 }}>No users yet.</div>
            <div style={{ fontSize: 12 }}>
              Click <span style={{ color: 'var(--t-accent)', fontWeight: 600 }}>+ Invite User</span> above to add your first team member.
            </div>
          </div>
        )}
        {users.length > 0 && users.length < 3 && (
          <div style={{ padding: '8px 16px', fontSize: 12, color: 'var(--t-text-muted)', background: 'var(--t-panel-alt)', borderTop: '1px solid var(--t-border)' }}>
            Tip: Invite more team members using the <span style={{ color: 'var(--t-accent)' }}>+ Invite User</span> button above.
          </div>
        )}
      </div>
    </div>
  );
}
