import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import { api } from '../api/client';

interface NotificationGroup {
  id: number;
  tenant_id: number;
  name: string;
  description: string;
  member_count: number;
  created_at: string;
}

interface GroupMember {
  id: number;
  user_id: number | null;
  name: string;
  email: string;
  type: 'user' | 'external';
  created_at: string;
}

interface NotificationSettings {
  email_blocklist: string[];
  email_loop_detection: boolean;
  teams_webhook_enabled: boolean;
  teams_webhook_url: string;
  slack_webhook_url: string;
}

export interface NotificationPreference {
  id: number;
  event: string;
  channel: string;
  role_target: string;
  enabled: boolean;
}

interface NotificationState {
  groups: NotificationGroup[];
  activeGroupMembers: GroupMember[];
  activeGroupId: number | null;
  settings: NotificationSettings;
  preferences: NotificationPreference[];
  loading: boolean;

  loadGroups: () => Promise<void>;
  createGroup: (name: string, description?: string) => Promise<void>;
  updateGroup: (id: number, data: { name?: string; description?: string }) => Promise<void>;
  deleteGroup: (id: number) => Promise<void>;
  loadMembers: (groupId: number) => Promise<void>;
  addMember: (groupId: number, data: { user_id?: number; email?: string }) => Promise<void>;
  removeMember: (groupId: number, memberId: number) => Promise<void>;
  loadSettings: () => Promise<void>;
  updateSettings: (data: Partial<NotificationSettings>) => Promise<void>;
  loadPreferences: () => Promise<void>;
  updatePreference: (event: string, channel: string, role_target: string, enabled: boolean) => Promise<void>;
}

export const useNotificationStore = create<NotificationState>()(
  immer((set, get) => ({
    groups: [],
    activeGroupMembers: [],
    activeGroupId: null,
    settings: {
      email_blocklist: [],
      email_loop_detection: true,
      teams_webhook_enabled: true,
      teams_webhook_url: '',
      slack_webhook_url: '',
    },
    preferences: [],
    loading: false,

    loadGroups: async () => {
      set((s) => { s.loading = true; });
      try {
        const groups = await api.listNotificationGroups();
        set((s) => { s.groups = groups; s.loading = false; });
      } catch {
        set((s) => { s.loading = false; });
      }
    },

    createGroup: async (name, description) => {
      await api.createNotificationGroup({ name, description });
      await get().loadGroups();
    },

    updateGroup: async (id, data) => {
      await api.updateNotificationGroup(id, data);
      await get().loadGroups();
    },

    deleteGroup: async (id) => {
      await api.deleteNotificationGroup(id);
      set((s) => {
        if (s.activeGroupId === id) {
          s.activeGroupId = null;
          s.activeGroupMembers = [];
        }
      });
      await get().loadGroups();
    },

    loadMembers: async (groupId) => {
      const members = await api.listGroupMembers(groupId);
      set((s) => { s.activeGroupMembers = members; s.activeGroupId = groupId; });
    },

    addMember: async (groupId, data) => {
      await api.addGroupMember(groupId, data);
      await get().loadMembers(groupId);
      await get().loadGroups();
    },

    removeMember: async (groupId, memberId) => {
      await api.removeGroupMember(groupId, memberId);
      await get().loadMembers(groupId);
      await get().loadGroups();
    },

    loadSettings: async () => {
      try {
        const settings = await api.getNotificationSettings();
        set((s) => { s.settings = settings; });
      } catch {}
    },

    updateSettings: async (data) => {
      const merged = { ...get().settings, ...data };
      await api.updateNotificationSettings(merged);
      set((s) => { Object.assign(s.settings, data); });
    },

    loadPreferences: async () => {
      try {
        const prefs = await api.getNotificationPreferences();
        set((s) => { s.preferences = prefs; });
      } catch {}
    },

    updatePreference: async (event, channel, role_target, enabled) => {
      // Optimistic update
      set((s) => {
        const pref = s.preferences.find(
          (p) => p.event === event && p.channel === channel && p.role_target === role_target
        );
        if (pref) pref.enabled = enabled;
      });
      await api.updateNotificationPreferences([{ event, channel, role_target, enabled }]);
    },
  }))
);
