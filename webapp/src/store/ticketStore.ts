import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import { api } from '../api/client';
import type { Ticket, TicketComment, TicketStatus, TicketPriority, TagSuggestion, SlaStatus, TicketSortField, Agent } from '../types';

interface TicketFilters {
  status?: TicketStatus;
  priority?: TicketPriority;
  assignee_id?: string;
  requester_id?: string;
  location_id?: string;
  problem_category_id?: string;
  team_id?: string;
  ticket_type?: string;
  tag?: string;
  sla_status?: SlaStatus;
  created_after?: string;
  created_before?: string;
  search?: string;
}

type StatusScope = 'active' | 'resolved' | 'all';

// Column visibility config
const DEFAULT_VISIBLE_COLUMNS = ['ticket', 'priority', 'subject', 'status', 'sla', 'age', 'last_reply', 'assignee'];

function loadVisibleColumns(): string[] {
  try {
    const saved = localStorage.getItem('helpdesk_visible_columns');
    if (saved) return JSON.parse(saved);
  } catch {}
  return DEFAULT_VISIBLE_COLUMNS;
}

function loadStatusScope(): StatusScope {
  try {
    const saved = localStorage.getItem('helpdesk_status_scope');
    if (saved === 'active' || saved === 'resolved' || saved === 'all') return saved;
  } catch {}
  return 'active';
}

interface TicketState {
  tickets: Ticket[];
  total: number;
  loading: boolean;
  error: string | null;
  filters: TicketFilters;
  sortBy: TicketSortField;
  sortDir: 'asc' | 'desc';
  activeTicket: Ticket | null;
  activeComments: TicketComment[];
  tagSuggestions: TagSuggestion[];
  viewMode: 'board' | 'list';
  agents: Agent[];
  statusScope: StatusScope;
  myTicketsOnly: boolean;
  visibleColumns: string[];
  scopeCounts: Record<string, number>;  // KPI counts — stable across status filter clicks

  loadTickets: () => Promise<void>;
  loadTicket: (id: number) => Promise<void>;
  setFilters: (filters: Partial<TicketFilters>) => void;
  clearFilters: () => void;
  setSortBy: (field: TicketSortField, dir?: 'asc' | 'desc') => void;
  setViewMode: (mode: 'board' | 'list') => void;
  setStatusScope: (scope: StatusScope) => void;
  toggleMyTickets: () => void;
  setVisibleColumns: (columns: string[]) => void;
  createTicket: (data: Partial<Ticket>) => Promise<{ id: number; ticket_number: string }>;
  updateTicket: (id: number, data: Partial<Ticket>) => Promise<void>;
  addComment: (ticketId: number, content: string, isInternal?: boolean, sendEmail?: boolean, cc?: string[], attachmentIds?: number[]) => Promise<void>;
  refreshComments: (ticketId: number) => Promise<void>;
  loadAgents: () => Promise<void>;
}

export const useTicketStore = create<TicketState>()(
  immer((set, get) => ({
    tickets: [],
    total: 0,
    loading: false,
    error: null,
    filters: {},
    sortBy: 'priority_age',
    sortDir: 'asc',
    activeTicket: null,
    activeComments: [],
    tagSuggestions: [],
    viewMode: 'board',
    agents: [],
    statusScope: loadStatusScope(),
    myTicketsOnly: false,
    visibleColumns: loadVisibleColumns(),
    scopeCounts: {},

    loadTickets: async () => {
      // Only show loading spinner on first load (not on background polls)
      set((s) => { if (s.tickets.length === 0) s.loading = true; s.error = null; });
      try {
        const params: Record<string, string> = { limit: '200' };
        const { filters, sortBy, sortDir, statusScope, myTicketsOnly } = get();

        // Status scope → status_in param (unless user set an explicit status filter)
        if (!filters.status) {
          if (statusScope === 'active') params.status_in = 'open,pending';
          else if (statusScope === 'resolved') params.status_in = 'resolved,closed_not_resolved';
          // 'all' → no status filter
        }

        // "My Tickets" toggle
        if (myTicketsOnly) params.assignee_id = '__me__';

        // Serialize filters
        if (filters.status) params.status = filters.status;
        if (filters.priority) params.priority = filters.priority;
        if (filters.assignee_id && !myTicketsOnly) params.assignee_id = filters.assignee_id;
        if (filters.requester_id) params.requester_id = filters.requester_id;
        if (filters.location_id) params.location_id = filters.location_id;
        if (filters.problem_category_id) params.problem_category_id = filters.problem_category_id;
        if (filters.tag) params.tag = filters.tag;
        if (filters.sla_status) params.sla_status = filters.sla_status;
        if (filters.created_after) params.created_after = filters.created_after;
        if (filters.created_before) params.created_before = filters.created_before;
        if (filters.search) params.search = filters.search;

        // Serialize sort
        params.sort_by = sortBy;
        params.sort_dir = sortDir;

        const result = await api.listTickets(params);
        set((s) => {
          s.tickets = result.tickets;
          s.total = result.total;
          s.loading = false;
          // Update KPI scope counts only when no single-status filter is active
          // (so clicking a KPI card doesn't change the KPI numbers)
          if (!s.filters.status) {
            const counts: Record<string, number> = {};
            for (const t of result.tickets) {
              counts[t.status] = (counts[t.status] || 0) + 1;
            }
            s.scopeCounts = counts;
          }
        });
      } catch (e: any) {
        set((s) => { s.error = e.message; s.loading = false; });
      }
    },

    loadTicket: async (id: number) => {
      // Pre-populate from list data so the UI renders instantly (no loading flash)
      const existing = get().tickets.find((t) => t.id === id);
      if (existing && get().activeTicket?.id !== id) {
        set((s) => { s.activeTicket = existing; });
      }
      try {
        const result = await api.getTicket(id);
        set((s) => {
          s.activeTicket = result.ticket;
          s.activeComments = result.comments;
          s.tagSuggestions = result.tag_suggestions || [];
        });
      } catch (e: any) {
        set((s) => { s.error = e.message; });
      }
    },

    setFilters: (filters) => {
      set((s) => { Object.assign(s.filters, filters); });
      get().loadTickets();
    },

    clearFilters: () => {
      set((s) => { s.filters = {}; });
      get().loadTickets();
    },

    setSortBy: (field, dir) => {
      set((s) => {
        if (s.sortBy === field && !dir) {
          // Toggle direction
          s.sortDir = s.sortDir === 'asc' ? 'desc' : 'asc';
        } else {
          s.sortBy = field;
          s.sortDir = dir || (field === 'priority_age' ? 'asc' : 'desc');
        }
      });
      get().loadTickets();
    },

    setViewMode: (mode) => set((s) => { s.viewMode = mode; }),

    setStatusScope: (scope) => {
      set((s) => {
        s.statusScope = scope;
        // Clear explicit status filter when switching scope
        delete s.filters.status;
      });
      localStorage.setItem('helpdesk_status_scope', scope);
      get().loadTickets();
    },

    toggleMyTickets: () => {
      set((s) => { s.myTicketsOnly = !s.myTicketsOnly; });
      get().loadTickets();
    },

    setVisibleColumns: (columns) => {
      set((s) => { s.visibleColumns = columns; });
      localStorage.setItem('helpdesk_visible_columns', JSON.stringify(columns));
    },

    createTicket: async (data) => {
      const result = await api.createTicket(data);
      await get().loadTickets();
      return result;
    },

    updateTicket: async (id, data) => {
      await api.updateTicket(id, data);
      await get().loadTickets();
      if (get().activeTicket?.id === id) {
        await get().loadTicket(id);
      }
    },

    addComment: async (ticketId, content, isInternal = false, sendEmail = false, cc = [], attachmentIds = []) => {
      await api.addComment(ticketId, { content, is_internal: isInternal, send_email: sendEmail, cc, attachment_ids: attachmentIds });
      if (get().activeTicket?.id === ticketId) {
        await get().loadTicket(ticketId);
      }
    },

    refreshComments: async (ticketId) => {
      try {
        // Fetch full ticket detail (comments + tag suggestions) so pipeline results appear
        const result = await api.getTicket(ticketId);
        // Only update if this is still the active ticket (avoid stale overwrites)
        if (get().activeTicket?.id === ticketId) {
          set((s) => {
            s.activeComments = result.comments;
            s.tagSuggestions = result.tag_suggestions || [];
          });
        }
      } catch {
        // Silent fail — polling shouldn't disrupt the UI
      }
    },

    loadAgents: async () => {
      try {
        const agents = await api.listAgents();
        set((s) => { s.agents = agents; });
      } catch (e: any) {
        console.warn('Failed to load agents:', e);
      }
    },
  }))
);
