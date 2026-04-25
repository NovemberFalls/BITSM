import { create } from 'zustand';
import { pushUrl, stripSlug } from '../utils/url';

type View = 'tickets' | 'kb' | 'chat' | 'admin' | 'audit' | 'reports' | 'automations' | 'sprints' | 'portal';

interface UIState {
  activeView: View;
  sidebarHovered: boolean;
  createTicketOpen: boolean;
  ticketDetailId: number | null;

  setView: (view: View) => void;
  setSidebarHovered: (hovered: boolean) => void;
  setCreateTicketOpen: (open: boolean) => void;
  openTicketDetail: (id: number) => void;
  closeTicketDetail: () => void;
}

export type { View };

export const useUIStore = create<UIState>((set) => ({
  activeView: 'tickets',
  sidebarHovered: false,
  createTicketOpen: false,
  ticketDetailId: null,

  setView: (view) => set({ activeView: view, ticketDetailId: null }),
  setSidebarHovered: (hovered) => set({ sidebarHovered: hovered }),
  setCreateTicketOpen: (open) => set({ createTicketOpen: open }),
  openTicketDetail: (id) => {
    set({ ticketDetailId: id });
    pushUrl(`/tickets/${id}`);
  },
  closeTicketDetail: () => {
    set({ ticketDetailId: null });
    const stripped = stripSlug(window.location.pathname);
    if (stripped.startsWith('/tickets/')) {
      pushUrl('/tickets');
    }
    // Sprint item paths — go back to sprint board or tasks tab
    const boardItem = stripped.match(/^\/sprints\/(\d+)\/items\/\d+$/);
    if (boardItem) {
      pushUrl(`/sprints/${boardItem[1]}`);
      return;
    }
    if (stripped.match(/^\/sprints\/items\/\d+$/)) {
      pushUrl('/sprints', 'tab=tasks');
    }
  },
}));
