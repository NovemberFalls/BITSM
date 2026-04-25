import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import { DateRangePicker } from './DateRangePicker';
import { LocationFilter } from './LocationFilter';

interface VolumeRow {
  period: string;
  total: number;
  open: number;
  pending: number;
  resolved: number;
  closed_not_resolved: number;
}

interface AgentBreakdown {
  agent_name: string;
  role: string;
  ticket_count: number;
  resolved_count: number;
  open_count: number;
  pending_count: number;
  avg_resolution_hours: number | null;
}

type GroupBy = 'day' | 'week' | 'month';

function daysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().split('T')[0];
}

function formatPeriod(value: string): string {
  const d = new Date(value);
  if (isNaN(d.getTime())) return value;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

const ROLE_LABEL: Record<string, string> = {
  super_admin: 'Super Admin',
  tenant_admin: 'Admin',
  agent: 'Agent',
};

export function TicketVolume({ canExport, teamId }: { canExport: boolean; teamId?: string }) {
  const [rows, setRows] = useState<VolumeRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [startDate, setStartDate] = useState(daysAgo(30));
  const [endDate, setEndDate] = useState(new Date().toISOString().split('T')[0]);
  const [groupBy, setGroupBy] = useState<GroupBy>('day');
  const [locationId, setLocationId] = useState('');
  const [expandedPeriod, setExpandedPeriod] = useState<string | null>(null);
  const [drillLoading, setDrillLoading] = useState(false);
  const [drillData, setDrillData] = useState<AgentBreakdown[]>([]);

  const load = async () => {
    setLoading(true);
    setExpandedPeriod(null);
    try {
      const params: Record<string, string> = { start_date: startDate, end_date: endDate, group_by: groupBy };
      if (locationId) params.location_id = locationId;
      if (teamId) params.team_id = teamId;
      const data = await api.getTicketVolumeReport(params);
      setRows(data.rows || []);
    } catch { /* empty */ }
    setLoading(false);
  };

  useEffect(() => { load(); }, [startDate, endDate, groupBy, locationId, teamId]);

  const toggleDrill = async (period: string) => {
    if (expandedPeriod === period) {
      setExpandedPeriod(null);
      return;
    }
    setExpandedPeriod(period);
    setDrillLoading(true);
    try {
      const params: Record<string, string> = { period, group_by: groupBy };
      if (locationId) params.location_id = locationId;
      const data = await api.getTicketVolumeBreakdown(params);
      setDrillData(data.agents || []);
    } catch { /* empty */ }
    setDrillLoading(false);
  };

  const total = rows.reduce((s, r) => s + (r.total || 0), 0);
  const totalOpen = rows.reduce((s, r) => s + (r.open || 0), 0);
  const totalPending = rows.reduce((s, r) => s + (r.pending || 0), 0);
  const totalResolved = rows.reduce((s, r) => s + (r.resolved || 0), 0);
  const totalClosed = rows.reduce((s, r) => s + (r.closed_not_resolved || 0), 0);

  if (loading) return <div className="audit-empty">Loading ticket volume...</div>;

  // Shared grid — drill rows use same template so columns align perfectly
  const colWidths = 'minmax(140px,1fr) 70px 70px 80px 90px 95px 32px';

  return (
    <div>
      <div className="report-controls">
        <DateRangePicker startDate={startDate} endDate={endDate} onChange={(s, e) => { setStartDate(s); setEndDate(e); }} />
        <LocationFilter value={locationId} onChange={setLocationId} />
        <div className="report-group-toggle">
          {(['day', 'week', 'month'] as GroupBy[]).map(g => (
            <button key={g} className={`report-group-btn ${groupBy === g ? 'active' : ''}`} onClick={() => setGroupBy(g)}>
              {g.charAt(0).toUpperCase() + g.slice(1)}
            </button>
          ))}
        </div>
        {canExport && (
          <button
            className="report-csv-btn"
            onClick={() => {
              const params: Record<string, string> = { start_date: startDate, end_date: endDate, group_by: groupBy };
              if (locationId) params.location_id = locationId;
              api.exportReportCsv('ticket-volume', params);
            }}
            title="Export CSV"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3" />
            </svg>
            CSV
          </button>
        )}
      </div>

      {rows.length === 0 ? (
        <div className="audit-empty">No tickets in this period.</div>
      ) : (
        <div className="report-table-wrap">
          {/* Header */}
          <div className="report-table-header" style={{ display: 'grid', gridTemplateColumns: colWidths }}>
            <div className="report-table-cell report-table-th">Period</div>
            <div className="report-table-cell report-table-th" style={{ textAlign: 'right' }}>Total</div>
            <div className="report-table-cell report-table-th" style={{ textAlign: 'right' }}>Open</div>
            <div className="report-table-cell report-table-th" style={{ textAlign: 'right' }}>Pending</div>
            <div className="report-table-cell report-table-th" style={{ textAlign: 'right' }}>Resolved</div>
            <div className="report-table-cell report-table-th" style={{ textAlign: 'right' }}>Closed (NR)</div>
            <div className="report-table-cell report-table-th" />
          </div>

          {/* Data rows */}
          {rows.map(row => (
            <div key={row.period}>
              <div
                className="report-table-row report-row-clickable"
                style={{ display: 'grid', gridTemplateColumns: colWidths, cursor: 'pointer' }}
                onClick={() => toggleDrill(row.period)}
              >
                <div className="report-table-cell" style={{ color: 'var(--t-text-bright)', fontWeight: 500 }}>
                  {formatPeriod(row.period)}
                </div>
                <div className="report-table-cell" style={{ textAlign: 'right', fontWeight: 600 }}>{row.total}</div>
                <div className="report-table-cell" style={{ textAlign: 'right', color: row.open > 0 ? 'var(--t-info)' : undefined }}>{row.open}</div>
                <div className="report-table-cell" style={{ textAlign: 'right', color: row.pending > 0 ? 'var(--t-warning)' : undefined }}>{row.pending}</div>
                <div className="report-table-cell" style={{ textAlign: 'right', color: row.resolved > 0 ? 'var(--t-success)' : undefined }}>{row.resolved}</div>
                <div className="report-table-cell" style={{ textAlign: 'right' }}>{row.closed_not_resolved}</div>
                <div className="report-table-cell" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--t-text-dim)' }}>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                    style={{ transform: expandedPeriod === row.period ? 'rotate(180deg)' : undefined, transition: 'transform 0.15s' }}>
                    <path d="M6 9l6 6 6-6" />
                  </svg>
                </div>
              </div>

              {/* Drill-down panel — same grid as parent so columns align */}
              {expandedPeriod === row.period && (
                <div className="report-drill-panel">
                  {drillLoading ? (
                    <div className="report-drill-loading">Loading breakdown...</div>
                  ) : drillData.length === 0 ? (
                    <div className="report-drill-loading">No assigned tickets in this period.</div>
                  ) : (
                    <>
                      <div className="report-drill-header" style={{ gridTemplateColumns: colWidths }}>
                        <span>Agent</span>
                        <span style={{ textAlign: 'right' }}>Tickets</span>
                        <span style={{ textAlign: 'right' }}>Open</span>
                        <span style={{ textAlign: 'right' }}>Pending</span>
                        <span style={{ textAlign: 'right' }}>Resolved</span>
                        <span style={{ textAlign: 'right' }}>Avg Res</span>
                        <span />
                      </div>
                      {drillData.map((a, i) => (
                        <div key={i} className="report-drill-row" style={{ gridTemplateColumns: colWidths }}>
                          <span>
                            <span style={{ fontWeight: 500 }}>{a.agent_name}</span>
                            <span style={{ marginLeft: 8, fontSize: 10, color: 'var(--t-text-dim)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.3px' }}>
                              {ROLE_LABEL[a.role] || a.role}
                            </span>
                          </span>
                          <span style={{ textAlign: 'right', fontWeight: 600 }}>{a.ticket_count}</span>
                          <span style={{ textAlign: 'right', color: a.open_count > 0 ? 'var(--t-info)' : 'var(--t-text-dim)' }}>{a.open_count}</span>
                          <span style={{ textAlign: 'right', color: a.pending_count > 0 ? 'var(--t-warning)' : 'var(--t-text-dim)' }}>{a.pending_count}</span>
                          <span style={{ textAlign: 'right', color: a.resolved_count > 0 ? 'var(--t-success)' : 'var(--t-text-dim)' }}>{a.resolved_count}</span>
                          <span style={{ textAlign: 'right', color: 'var(--t-text-muted)' }}>
                            {a.avg_resolution_hours != null ? `${a.avg_resolution_hours}h` : '—'}
                          </span>
                          <span />
                        </div>
                      ))}
                    </>
                  )}
                </div>
              )}
            </div>
          ))}

          {/* Summary row */}
          {rows.length > 0 && (
            <div className="report-table-row report-table-summary" style={{ display: 'grid', gridTemplateColumns: colWidths }}>
              <div className="report-table-cell" style={{ fontWeight: 600 }}>Total</div>
              <div className="report-table-cell" style={{ textAlign: 'right', fontWeight: 700 }}>{total}</div>
              <div className="report-table-cell" style={{ textAlign: 'right', fontWeight: 600 }}>{totalOpen}</div>
              <div className="report-table-cell" style={{ textAlign: 'right', fontWeight: 600 }}>{totalPending}</div>
              <div className="report-table-cell" style={{ textAlign: 'right', fontWeight: 600 }}>{totalResolved}</div>
              <div className="report-table-cell" style={{ textAlign: 'right', fontWeight: 600 }}>{totalClosed}</div>
              <div className="report-table-cell" />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
