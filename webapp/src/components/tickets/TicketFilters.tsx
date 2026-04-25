import { useState, useEffect } from 'react';
import { useTicketStore } from '../../store/ticketStore';
import { useHierarchyStore } from '../../store/hierarchyStore';
import { STATUS_OPTIONS, PRIORITY_OPTIONS, SLA_STATUS_OPTIONS } from '../../types';
import type { TicketSortField } from '../../types';
import { CascadingSelect } from '../common/CascadingSelect';
import { api } from '../../api/client';

const SORT_OPTIONS: { value: TicketSortField; label: string }[] = [
  { value: 'priority_age', label: 'Priority + Age' },
  { value: 'created_at', label: 'Date Created' },
  { value: 'sla_due_at', label: 'SLA Due Date' },
  { value: 'updated_at', label: 'Last Updated' },
  { value: 'priority', label: 'Priority' },
];

export function TicketFilters() {
  const { filters, setFilters, clearFilters, sortBy, setSortBy, agents, loadAgents } = useTicketStore();
  const { locations, problemCategories } = useHierarchyStore();
  const [expanded, setExpanded] = useState(false);
  const [teams, setTeams] = useState<any[]>([]);

  // Count active filters (excluding search)
  const activeCount = Object.entries(filters).filter(
    ([k, v]) => v && k !== 'search'
  ).length;

  const handleSearchChange = (value: string) => {
    if (!value) {
      const { search, ...rest } = filters;
      setFilters(rest);
      useTicketStore.setState((s) => { s.filters = rest; });
      useTicketStore.getState().loadTickets();
    } else {
      setFilters({ search: value });
    }
  };

  const handleExpand = () => {
    if (!expanded) {
      loadAgents();
      if (teams.length === 0) api.listTeams().then(setTeams).catch(() => {});
    }
    setExpanded(!expanded);
  };

  return (
    <div>
      {/* Compact bar: search + sort + filter toggle */}
      <div className="ticket-filter-bar">
        <input
          type="text"
          className="form-input filter-search"
          placeholder="Search tickets..."
          value={filters.search || ''}
          onChange={(e) => handleSearchChange(e.target.value)}
        />

        <select
          className="form-input form-select filter-select"
          value={sortBy}
          onChange={(e) => setSortBy(e.target.value as TicketSortField)}
        >
          {SORT_OPTIONS.map((s) => (
            <option key={s.value} value={s.value}>Sort: {s.label}</option>
          ))}
        </select>

        <button className="filter-toggle-btn" onClick={handleExpand}>
          {expanded ? '− Filters' : '+ Filters'}
          {activeCount > 0 && <span className="filter-badge">{activeCount}</span>}
        </button>

        {activeCount > 0 && (
          <button className="filter-clear-btn" onClick={clearFilters}>Clear all</button>
        )}
      </div>

      {/* All filters in expandable panel */}
      {expanded && (
        <div className="ticket-filter-expanded">
          <div className="filter-field">
            <label className="filter-field-label">Status</label>
            <select
              className="form-input form-select"
              value={filters.status || ''}
              onChange={(e) => setFilters({ status: e.target.value as any || undefined })}
            >
              <option value="">All</option>
              {STATUS_OPTIONS.map((s) => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </select>
          </div>

          <div className="filter-field">
            <label className="filter-field-label">Priority</label>
            <select
              className="form-input form-select"
              value={filters.priority || ''}
              onChange={(e) => setFilters({ priority: e.target.value as any || undefined })}
            >
              <option value="">All</option>
              {PRIORITY_OPTIONS.map((p) => (
                <option key={p.value} value={p.value}>{p.label}</option>
              ))}
            </select>
          </div>

          <div className="filter-field">
            <label className="filter-field-label">SLA</label>
            <select
              className="form-input form-select"
              value={filters.sla_status || ''}
              onChange={(e) => setFilters({ sla_status: e.target.value as any || undefined })}
            >
              <option value="">All</option>
              {SLA_STATUS_OPTIONS.map((s) => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </select>
          </div>

          <div className="filter-field">
            <label className="filter-field-label">Assignee</label>
            <select
              className="form-input form-select"
              value={filters.assignee_id || ''}
              onChange={(e) => setFilters({ assignee_id: e.target.value || undefined })}
            >
              <option value="">All</option>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </select>
          </div>

          <div className="filter-field">
            <label className="filter-field-label">Type</label>
            <select
              className="form-input form-select"
              value={filters.ticket_type || ''}
              onChange={(e) => setFilters({ ticket_type: e.target.value || undefined })}
            >
              <option value="">All</option>
              <option value="support">Support</option>
              <option value="task">Task</option>
              <option value="bug">Bug</option>
              <option value="feature">Feature</option>
            </select>
          </div>

          <div className="filter-field">
            <label className="filter-field-label">Team</label>
            <select
              className="form-input form-select"
              value={filters.team_id || ''}
              onChange={(e) => setFilters({ team_id: e.target.value || undefined })}
            >
              <option value="">All</option>
              {teams.map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </select>
          </div>

          <div className="filter-field">
            <label className="filter-field-label">Location</label>
            <CascadingSelect
              items={locations}
              value={filters.location_id ? parseInt(filters.location_id) : null}
              onChange={(id) => setFilters({ location_id: id ? String(id) : undefined })}
              placeholder="Any"
              allowNonLeaf
            />
          </div>

          <div className="filter-field">
            <label className="filter-field-label">Category</label>
            <CascadingSelect
              items={problemCategories}
              value={filters.problem_category_id ? parseInt(filters.problem_category_id) : null}
              onChange={(id) => setFilters({ problem_category_id: id ? String(id) : undefined })}
              placeholder="Any"
              allowNonLeaf
            />
          </div>

          <div className="filter-field">
            <label className="filter-field-label">Tag</label>
            <input
              type="text"
              className="form-input"
              value={filters.tag || ''}
              onChange={(e) => setFilters({ tag: e.target.value || undefined })}
              placeholder="Filter by tag"
            />
          </div>

          <div className="filter-field">
            <label className="filter-field-label">Created After</label>
            <input
              type="date"
              className="form-input"
              value={filters.created_after || ''}
              onChange={(e) => setFilters({ created_after: e.target.value || undefined })}
            />
          </div>

          <div className="filter-field">
            <label className="filter-field-label">Created Before</label>
            <input
              type="date"
              className="form-input"
              value={filters.created_before || ''}
              onChange={(e) => setFilters({ created_before: e.target.value || undefined })}
            />
          </div>

          <div className="filter-field">
            <label className="filter-field-label">Requester</label>
            <select
              className="form-input form-select"
              value={filters.requester_id || ''}
              onChange={(e) => setFilters({ requester_id: e.target.value || undefined })}
            >
              <option value="">All</option>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </select>
          </div>
        </div>
      )}
    </div>
  );
}
