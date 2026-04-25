import { useEffect, useMemo, useState, useRef } from 'react';
import { useTicketStore } from '../../store/ticketStore';
import { useUIStore } from '../../store/uiStore';
import { useAuthStore } from '../../store/authStore';
import type { Ticket, TicketStatus } from '../../types';
import { STATUS_OPTIONS, PRIORITY_OPTIONS } from '../../types';
import { TicketDetail } from './TicketDetail';
import { TicketFilters } from './TicketFilters';
import { formatDuration, slaStatusLabel } from '../../utils/time';
import { SetupChecklist } from '../SetupChecklist';

const COLUMNS: { status: TicketStatus; label: string }[] = STATUS_OPTIONS.map((s) => ({
  status: s.value,
  label: s.label,
}));

function priorityShort(p: string): string {
  const map: Record<string, string> = { p1: 'P1', p2: 'P2', p3: 'P3', p4: 'P4' };
  return map[p] || p;
}

/** Push view mode to URL without full reload */
function pushViewMode(mode: 'board' | 'list') {
  const base = window.location.pathname;
  window.history.replaceState(null, '', `${base}?view=${mode}`);
}

/* ============================================================
   COLUMN DEFINITIONS — configurable per user
   ============================================================ */
interface ColumnDef {
  key: string;
  label: string;
  width: string;     // CSS width
  canHide: boolean;
  defaultVisible: boolean;
  align?: 'center' | 'left' | 'right';
  sortField?: string;
}

const COLUMN_DEFS: ColumnDef[] = [
  { key: 'ticket',    label: 'Ticket',    width: '90px',  canHide: false, defaultVisible: true },
  { key: 'priority',  label: 'Pri',       width: '48px',  canHide: true,  defaultVisible: true, align: 'center' },
  { key: 'subject',   label: 'Subject',   width: '1fr',   canHide: false, defaultVisible: true },
  { key: 'status',    label: 'Status',    width: '90px',  canHide: true,  defaultVisible: true },
  { key: 'responded', label: 'Responded', width: '72px',  canHide: true,  defaultVisible: false, align: 'center' },
  { key: 'sla',       label: 'SLA',       width: '65px',  canHide: true,  defaultVisible: true },
  { key: 'age',       label: 'Age',       width: '72px',  canHide: true,  defaultVisible: true, sortField: 'created_at' },
  { key: 'last_reply',label: 'Reply',     width: '75px',  canHide: true,  defaultVisible: true },
  { key: 'assignee',  label: 'Assignee',  width: '100px', canHide: true,  defaultVisible: true },
];

/* ============================================================
   SCOPE TABS — Active / Resolved / All
   ============================================================ */
const SCOPE_TABS = [
  { key: 'active' as const,   label: 'Active' },
  { key: 'resolved' as const, label: 'Resolved' },
  { key: 'all' as const,      label: 'All' },
];

export function TicketBoard() {
  const {
    tickets, loading, viewMode, setViewMode, loadTickets,
    statusScope, setStatusScope, myTicketsOnly, toggleMyTickets,
    filters, setFilters, scopeCounts,
  } = useTicketStore();
  const ticketDetailId = useUIStore((s) => s.ticketDetailId);
  const userRole = useAuthStore((s) => s.user?.role);

  useEffect(() => {
    loadTickets();
  }, []);

  // Poll ticket list every 15s for seamless updates
  useEffect(() => {
    if (ticketDetailId) return;
    const interval = setInterval(() => { loadTickets(); }, 15000);
    return () => clearInterval(interval);
  }, [ticketDetailId]);

  // KPI metrics — use scopeCounts (stable across status filter clicks)
  // SLA violations always recalculate from current ticket list
  const metrics = useMemo(() => {
    let slaViolations = 0;
    for (const t of tickets) {
      if (t.sla_status === 'breached') slaViolations++;
    }
    return {
      statuses: STATUS_OPTIONS.map((s) => ({
        status: s.value,
        label: s.label,
        count: scopeCounts[s.value] || 0,
      })),
      slaViolations,
    };
  }, [scopeCounts, tickets]);

  const handleViewMode = (mode: 'board' | 'list') => {
    setViewMode(mode);
    pushViewMode(mode);
  };

  // KPI card click → filter to single status (toggle)
  const handleKpiClick = (status: TicketStatus) => {
    if (filters.status === status) {
      // Clear the single-status filter
      const { status: _, ...rest } = filters;
      useTicketStore.setState((s) => { s.filters = rest; });
      loadTickets();
    } else {
      setFilters({ status });
    }
  };

  // Kanban columns filtered by scope
  const visibleColumns = useMemo(() => {
    if (statusScope === 'active') return COLUMNS.filter(c => c.status === 'open' || c.status === 'pending');
    if (statusScope === 'resolved') return COLUMNS.filter(c => c.status === 'resolved' || c.status === 'closed_not_resolved');
    return COLUMNS;
  }, [statusScope]);

  // When a ticket is selected, render the full workspace
  if (ticketDetailId) {
    return <TicketDetail />;
  }

  return (
    <div>
      {/* Activation checklist for tenant admins */}
      {userRole === 'tenant_admin' && <SetupChecklist />}

      {/* KPI Metrics Row — clickable status shortcuts */}
      <div className="metrics-grid">
        {metrics.statuses.map((m) => (
          <div
            key={m.status}
            className={`metric-card ${filters.status === m.status ? 'metric-card-active' : ''}`}
            onClick={() => handleKpiClick(m.status as TicketStatus)}
            style={{ cursor: 'pointer' }}
          >
            <div className="metric-card-label">{m.label.toUpperCase()}</div>
            <div className="metric-card-value" style={{ color: m.count > 0 ? 'var(--t-accent-text)' : 'var(--t-text-muted)' }}>
              {m.count}
            </div>
          </div>
        ))}
        <div className="metric-card">
          <div className="metric-card-label">SLA VIOLATIONS</div>
          <div className="metric-card-value" style={{ color: metrics.slaViolations > 0 ? 'var(--t-error)' : 'var(--t-text-muted)' }}>
            {metrics.slaViolations}
          </div>
        </div>
      </div>

      {/* Filter bar */}
      <TicketFilters />

      {/* Scope tabs + My Tickets toggle + toolbar */}
      <div className="ticket-toolbar">
        <div className="ticket-toolbar-left">
          <div className="scope-tabs">
            {SCOPE_TABS.map((tab) => (
              <button
                key={tab.key}
                className={`scope-tab ${statusScope === tab.key ? 'scope-tab-active' : ''}`}
                onClick={() => setStatusScope(tab.key)}
              >
                {tab.label}
              </button>
            ))}
          </div>
          <button
            className={`btn btn-sm ${myTicketsOnly ? 'btn-primary' : 'btn-ghost'}`}
            onClick={toggleMyTickets}
            title="Show only tickets assigned to me"
          >
            My Tickets
          </button>
          <span className="ticket-count">{tickets.length} tickets</span>
        </div>
        <div className="ticket-toolbar-right">
          <button
            className={`btn btn-sm ${viewMode === 'board' ? 'btn-primary' : 'btn-ghost'}`}
            onClick={() => handleViewMode('board')}
          >
            Board
          </button>
          <button
            className={`btn btn-sm ${viewMode === 'list' ? 'btn-primary' : 'btn-ghost'}`}
            onClick={() => handleViewMode('list')}
          >
            List
          </button>
          {viewMode === 'list' && <ColumnConfigButton />}
        </div>
      </div>

      {loading && tickets.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon">&#x27F3;</div>
          <div className="empty-state-text">Loading tickets...</div>
        </div>
      ) : tickets.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon">&#x25A4;</div>
          <div className="empty-state-title">No tickets</div>
          <div className="empty-state-text">
            {statusScope === 'active' ? 'No active tickets. Try switching to "All" or "Resolved".' : 'No tickets match your current filters.'}
          </div>
        </div>
      ) : viewMode === 'list' ? (
        <TicketList tickets={tickets} />
      ) : (
        <div className="kanban-board">
          {visibleColumns.map((col) => {
            const colTickets = tickets
              .filter((t) => t.status === col.status)
              .sort((a, b) => {
                const po: Record<string, number> = { p1: 0, p2: 1, p3: 2, p4: 3 };
                const pa = po[a.priority] ?? 9;
                const pb = po[b.priority] ?? 9;
                if (pa !== pb) return pa - pb;
                return new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
              });
            return (
              <div key={col.status} className="kanban-column">
                <div className="kanban-column-header">
                  <span className="kanban-column-title">{col.label.toUpperCase()}</span>
                  <span className="kanban-column-count">{colTickets.length}</span>
                </div>
                <div className="kanban-cards">
                  {colTickets.map((ticket) => (
                    <KanbanCard key={ticket.id} ticket={ticket} />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ============================================================
   KANBAN CARD
   ============================================================ */
function KanbanCard({ ticket }: { ticket: Ticket }) {
  const openDetail = useUIStore((s) => s.openTicketDetail);
  return (
    <div className="ticket-card" onClick={() => openDetail(ticket.id)}>
      <div className="ticket-card-header">
        <span className="ticket-card-number">{ticket.ticket_number}</span>
        <span className={`badge badge-${ticket.priority} badge-sm`}>{priorityShort(ticket.priority)}</span>
      </div>
      <div className="ticket-card-subject">{ticket.subject}</div>
      <div className="ticket-card-grid">
        <div className="ticket-card-field">
          <span className="ticket-card-label">Responder</span>
          <span className="ticket-card-value">{ticket.last_responder || '—'}</span>
        </div>
        <div className="ticket-card-field">
          <span className="ticket-card-label">Age</span>
          <span className="ticket-card-value">{formatDuration(ticket.age_seconds)}</span>
        </div>
        <div className="ticket-card-field">
          <span className="ticket-card-label">Assignee</span>
          <span className="ticket-card-value">{ticket.assignee_name || 'Unassigned'}</span>
        </div>
        <div className="ticket-card-field">
          <span className="ticket-card-label">SLA</span>
          <span className="ticket-card-value">
            {ticket.sla_status && ticket.sla_status !== 'no_sla'
              ? <span className={`badge-sla badge-sla-${ticket.sla_status}`} style={{ fontSize: 10 }}>{slaStatusLabel(ticket.sla_status)}</span>
              : '—'}
          </span>
        </div>
      </div>
    </div>
  );
}

/* ============================================================
   COLUMN CONFIG BUTTON — gear icon with dropdown
   ============================================================ */
function ColumnConfigButton() {
  const [open, setOpen] = useState(false);
  const { visibleColumns, setVisibleColumns } = useTicketStore();
  const ref = useRef<HTMLDivElement>(null);

  // Close on click outside
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const toggle = (key: string) => {
    const next = visibleColumns.includes(key)
      ? visibleColumns.filter(k => k !== key)
      : [...visibleColumns, key];
    setVisibleColumns(next);
  };

  return (
    <div className="col-config-wrapper" ref={ref}>
      <button className="btn btn-sm btn-ghost" onClick={() => setOpen(!open)} title="Configure columns">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
          <circle cx="7" cy="7" r="2.5" />
          <path d="M7 1v1.5M7 11.5V13M1 7h1.5M11.5 7H13M2.8 2.8l1.1 1.1M10.1 10.1l1.1 1.1M2.8 11.2l1.1-1.1M10.1 3.9l1.1-1.1" />
        </svg>
      </button>
      {open && (
        <div className="col-config-dropdown">
          <div className="col-config-title">Visible Columns</div>
          {COLUMN_DEFS.filter(c => c.canHide).map(col => (
            <label key={col.key} className="col-config-item">
              <input
                type="checkbox"
                checked={visibleColumns.includes(col.key)}
                onChange={() => toggle(col.key)}
              />
              <span>{col.label}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

/* ============================================================
   LIST VIEW — configurable columns
   ============================================================ */
function TicketList({ tickets }: { tickets: Ticket[] }) {
  const openDetail = useUIStore((s) => s.openTicketDetail);
  const { sortBy, setSortBy, visibleColumns } = useTicketStore();

  const cols = COLUMN_DEFS.filter(c => visibleColumns.includes(c.key));

  const sortIcon = (field: string) => {
    if (sortBy !== field) return '';
    return useTicketStore.getState().sortDir === 'asc' ? ' \u25B2' : ' \u25BC';
  };

  const renderCell = (ticket: Ticket, col: ColumnDef) => {
    switch (col.key) {
      case 'ticket':
        return <span className="mono-text">{ticket.ticket_number}</span>;
      case 'priority':
        return <span className={`badge badge-${ticket.priority} badge-sm`}>{priorityShort(ticket.priority)}</span>;
      case 'subject':
        return <>{ticket.subject}</>;
      case 'status':
        return <span className={`badge badge-${ticket.status}`}>{STATUS_OPTIONS.find(s => s.value === ticket.status)?.label || ticket.status}</span>;
      case 'responded':
        return ticket.first_response_at
          ? <span className="badge badge-responded">Yes</span>
          : <span className="badge badge-awaiting">No</span>;
      case 'sla':
        return ticket.sla_status && ticket.sla_status !== 'no_sla'
          ? <span className={`badge-sla badge-sla-${ticket.sla_status}`}>{slaStatusLabel(ticket.sla_status)}</span>
          : null;
      case 'age':
        return <span className="mono-text">{formatDuration(ticket.age_seconds)}</span>;
      case 'last_reply':
        return <>{ticket.last_responder || '—'}</>;
      case 'assignee':
        return <>{ticket.assignee_name || 'Unassigned'}</>;
      default:
        return null;
    }
  };

  // Build CSS grid template from visible columns
  const gridTemplate = cols.map(c => c.width === '1fr' ? 'minmax(200px, 1fr)' : c.width).join(' ');

  return (
    <div className="ticket-list">
      <div className="ticket-list-header" style={{ gridTemplateColumns: gridTemplate }}>
        {cols.map(col => (
          <span
            key={col.key}
            className={`ticket-list-col ${col.sortField ? 'sortable' : ''}`}
            style={{ textAlign: col.align || 'left' }}
            onClick={col.sortField ? () => setSortBy(col.sortField as any) : undefined}
          >
            {col.label}{col.sortField ? sortIcon(col.sortField) : ''}
          </span>
        ))}
      </div>
      {tickets.map((t) => (
        <div key={t.id} className="ticket-list-row" onClick={() => openDetail(t.id)} style={{ gridTemplateColumns: gridTemplate }}>
          {cols.map(col => (
            <span
              key={col.key}
              className={`ticket-list-col ${col.key === 'subject' ? 'col-subject-cell' : ''}`}
              style={{ textAlign: col.align || 'left' }}
            >
              {renderCell(t, col)}
            </span>
          ))}
        </div>
      ))}
    </div>
  );
}
