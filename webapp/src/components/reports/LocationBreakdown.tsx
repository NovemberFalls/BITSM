import { useEffect, useMemo, useCallback, useState } from 'react';
import { api } from '../../api/client';
import { DateRangePicker } from './DateRangePicker';

interface LocationRow {
  location_name: string;
  location_id: number | null;
  parent_id: number | null;
  parent_name: string | null;
  ticket_count: number;
  open_count: number;
  resolved_count: number;
  avg_resolution_hours: number | null;
  breach_count: number;
  breach_rate: number | null;
}

interface TreeNode extends LocationRow {
  children: TreeNode[];
  depth: number;
}

type SortKey = 'ticket_count' | 'open_count' | 'avg_resolution_hours' | 'breach_rate' | 'location_name';
type SortDir = 'asc' | 'desc';

function buildTree(rows: LocationRow[], sortKey: SortKey, sortDir: SortDir): TreeNode[] {
  const noLoc = rows.filter(r => r.location_id == null);
  const withLoc = rows.filter(r => r.location_id != null);
  const knownIds = new Set(withLoc.map(r => r.location_id));

  const byParent: Record<string, LocationRow[]> = {};
  for (const r of withLoc) {
    // If parent is not in our result set, treat as root
    const parentKey = (r.parent_id != null && knownIds.has(r.parent_id)) ? String(r.parent_id) : '__root__';
    (byParent[parentKey] = byParent[parentKey] || []).push(r);
  }

  const cmp = (a: LocationRow, b: LocationRow): number => {
    const av = a[sortKey], bv = b[sortKey];
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    const c = typeof av === 'number' ? (av as number) - (bv as number) : String(av).localeCompare(String(bv));
    return sortDir === 'asc' ? c : -c;
  };

  function walk(parentKey: string, depth: number): TreeNode[] {
    return (byParent[parentKey] || []).sort(cmp).map(r => ({
      ...r, depth, children: walk(String(r.location_id), depth + 1),
    }));
  }

  const roots = walk('__root__', 0);
  if (noLoc.length > 0) roots.push({ ...noLoc[0], depth: 0, children: [] });
  return roots;
}

function flattenVisible(nodes: TreeNode[], expanded: Set<number | null>): TreeNode[] {
  const out: TreeNode[] = [];
  function walk(list: TreeNode[]) {
    for (const n of list) {
      out.push(n);
      if (n.children.length > 0 && expanded.has(n.location_id)) walk(n.children);
    }
  }
  walk(nodes);
  return out;
}

function daysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().split('T')[0];
}

function SortHeader({ label, col, sortKey, sortDir, onSort, align = 'right' }: {
  label: string; col: SortKey; sortKey: SortKey; sortDir: SortDir;
  onSort: (k: SortKey) => void; align?: string;
}) {
  const active = sortKey === col;
  return (
    <div onClick={() => onSort(col)} style={{ textAlign: align as any, cursor: 'pointer', userSelect: 'none', display: 'flex', alignItems: 'center', justifyContent: align === 'right' ? 'flex-end' : 'flex-start', gap: 3 }} className={`report-table-cell ${active ? 'report-th-active' : ''}`}>
      {label}
      <svg width="10" height="10" viewBox="0 0 10 10" fill="none" style={{ opacity: active ? 1 : 0.3 }}>
        {active && sortDir === 'asc' ? <path d="M5 2L8 7H2L5 2Z" fill="currentColor" /> : <path d="M5 8L2 3H8L5 8Z" fill="currentColor" />}
      </svg>
    </div>
  );
}

const colGrid = '1fr 72px 72px 90px 95px 110px 85px';

export function LocationBreakdown({ canExport, teamId }: { canExport: boolean; teamId?: string }) {
  const [rows, setRows] = useState<LocationRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [startDate, setStartDate] = useState(daysAgo(30));
  const [endDate, setEndDate] = useState(new Date().toISOString().split('T')[0]);
  const [sortKey, setSortKey] = useState<SortKey>('ticket_count');
  const [sortDir, setSortDir] = useState<SortDir>('desc');
  const [expandedIds, setExpandedIds] = useState<Set<number | null>>(new Set());

  const load = async () => {
    setLoading(true);
    setExpandedIds(new Set());
    try {
      const params: Record<string, string> = { start_date: startDate, end_date: endDate };
      if (teamId) params.team_id = teamId;
      const data = await api.getLocationBreakdown(params);
      setRows(data.rows || []);
    } catch { /* empty */ }
    setLoading(false);
  };

  useEffect(() => { load(); }, [startDate, endDate, teamId]);

  const handleSort = (k: SortKey) => {
    if (sortKey === k) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortKey(k); setSortDir('desc'); }
  };

  const toggle = useCallback((id: number | null) => setExpandedIds(prev => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  }), []);

  const tree = useMemo(() => buildTree(rows, sortKey, sortDir), [rows, sortKey, sortDir]);
  const flatRows = useMemo(() => flattenVisible(tree, expandedIds), [tree, expandedIds]);

  const totalTickets = rows.reduce((s, r) => s + (r.ticket_count || 0), 0);
  const totalOpen = rows.reduce((s, r) => s + (r.open_count || 0), 0);
  const totalResolved = rows.reduce((s, r) => s + (r.resolved_count || 0), 0);
  const totalBreaches = rows.reduce((s, r) => s + (r.breach_count || 0), 0);

  if (loading) return <div className="audit-empty">Loading location breakdown...</div>;

  return (
    <div>
      <div className="report-controls">
        <DateRangePicker startDate={startDate} endDate={endDate} onChange={(s, e) => { setStartDate(s); setEndDate(e); }} />
        {canExport && (
          <button className="report-csv-btn" style={{ marginLeft: 'auto' }}
            onClick={() => api.exportReportCsv('location-breakdown', { start_date: startDate, end_date: endDate })}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3" />
            </svg>
            CSV
          </button>
        )}
      </div>

      {flatRows.length === 0 ? (
        <div className="audit-empty">No ticket data for the selected period.</div>
      ) : (
        <div className="report-table">
          <div className="report-table-header" style={{ display: 'grid', gridTemplateColumns: colGrid }}>
            <SortHeader label="Location" col="location_name" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} align="left" />
            <SortHeader label="Total" col="ticket_count" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
            <SortHeader label="Open" col="open_count" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
            <div className="report-table-cell right">Resolved</div>
            <SortHeader label="Avg Res" col="avg_resolution_hours" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
            <div className="report-table-cell right">SLA Breach</div>
            <SortHeader label="Breach %" col="breach_rate" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
          </div>

          {flatRows.map((row, i) => (
            <div key={i} className="report-table-row" style={{ display: 'grid', gridTemplateColumns: colGrid }}>
              <div
                className="report-table-cell"
                style={{ paddingLeft: row.depth * 20, display: 'flex', alignItems: 'center', gap: 4, cursor: row.children.length > 0 ? 'pointer' : undefined }}
                onClick={row.children.length > 0 ? () => toggle(row.location_id) : undefined}
              >
                {row.children.length > 0 ? (
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
                    style={{ flexShrink: 0, color: 'var(--t-text-dim)', transition: 'transform 0.15s', transform: expandedIds.has(row.location_id) ? 'rotate(0deg)' : 'rotate(-90deg)' }}>
                    <path d="M6 9l6 6 6-6" />
                  </svg>
                ) : row.depth > 0 ? (
                  <span style={{ color: 'var(--t-text-dim)', fontSize: 11, flexShrink: 0 }}>└</span>
                ) : null}
                <span style={{ fontWeight: row.depth === 0 ? 500 : 400, color: row.depth === 0 ? 'var(--t-text-bright)' : undefined, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {row.location_name}
                </span>
                {row.children.length > 0 && (
                  <span style={{ fontSize: 10, color: 'var(--t-text-dim)', flexShrink: 0 }}>({row.children.length})</span>
                )}
              </div>
              <div className="report-table-cell right" style={{ fontWeight: row.depth === 0 ? 600 : 400 }}>{row.ticket_count.toLocaleString()}</div>
              <div className="report-table-cell right" style={{ color: row.open_count > 0 ? 'var(--t-info)' : undefined }}>{row.open_count}</div>
              <div className="report-table-cell right" style={{ color: row.resolved_count > 0 ? 'var(--t-success)' : undefined }}>{row.resolved_count}</div>
              <div className="report-table-cell right" style={{ color: 'var(--t-text-muted)' }}>
                {row.avg_resolution_hours != null ? `${Number(row.avg_resolution_hours).toFixed(1)}h` : '—'}
              </div>
              <div className="report-table-cell right" style={{ color: row.breach_count > 0 ? 'var(--t-error)' : undefined }}>{row.breach_count}</div>
              <div className="report-table-cell right">
                {row.breach_rate != null ? (
                  <span style={{ color: Number(row.breach_rate) >= 25 ? 'var(--t-error)' : Number(row.breach_rate) >= 10 ? 'var(--t-warning)' : 'var(--t-success)', fontWeight: 600 }}>
                    {Number(row.breach_rate).toFixed(1)}%
                  </span>
                ) : '—'}
              </div>
            </div>
          ))}

          <div className="report-table-summary" style={{ display: 'grid', gridTemplateColumns: colGrid }}>
            <div className="report-table-cell">Total</div>
            <div className="report-table-cell right">{totalTickets.toLocaleString()}</div>
            <div className="report-table-cell right">{totalOpen}</div>
            <div className="report-table-cell right">{totalResolved}</div>
            <div className="report-table-cell right" />
            <div className="report-table-cell right">{totalBreaches}</div>
            <div className="report-table-cell right" />
          </div>
        </div>
      )}
    </div>
  );
}
