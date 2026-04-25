import { useEffect, useMemo, useCallback, useState } from 'react';
import { api } from '../../api/client';
import { DateRangePicker } from './DateRangePicker';
import { LocationFilter } from './LocationFilter';

interface CategoryRow {
  category_id: number;
  parent_id: number | null;
  category_name: string;
  ticket_count: number;
  avg_resolution_hours: number | null;
}

interface TreeNode extends CategoryRow {
  children: TreeNode[];
  depth: number;
}

type SortKey = 'ticket_count' | 'avg_resolution_hours' | 'category_name';
type SortDir = 'asc' | 'desc';

function buildTree(rows: CategoryRow[], sortKey: SortKey, sortDir: SortDir): TreeNode[] {
  const byParent: Record<string, CategoryRow[]> = {};
  for (const r of rows) {
    const key = r.parent_id == null ? '__root__' : String(r.parent_id);
    (byParent[key] = byParent[key] || []).push(r);
  }

  const cmp = (a: CategoryRow, b: CategoryRow): number => {
    const av = a[sortKey], bv = b[sortKey];
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    const c = typeof av === 'number' ? (av as number) - (bv as number) : String(av).localeCompare(String(bv));
    return sortDir === 'asc' ? c : -c;
  };

  function walk(parentKey: string, depth: number): TreeNode[] {
    return (byParent[parentKey] || []).sort(cmp).map(r => ({
      ...r,
      depth,
      children: walk(String(r.category_id), depth + 1),
    }));
  }

  return walk('__root__', 0);
}

function flattenVisible(nodes: TreeNode[], expanded: Set<number>): TreeNode[] {
  const out: TreeNode[] = [];
  function walk(list: TreeNode[]) {
    for (const n of list) {
      out.push(n);
      if (n.children.length > 0 && expanded.has(n.category_id)) walk(n.children);
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
    <div
      onClick={() => onSort(col)}
      style={{ textAlign: align as any, cursor: 'pointer', userSelect: 'none', display: 'flex', alignItems: 'center', justifyContent: align === 'right' ? 'flex-end' : 'flex-start', gap: 3 }}
      className={`report-table-cell ${active ? 'report-th-active' : ''}`}
    >
      {label}
      <svg width="10" height="10" viewBox="0 0 10 10" fill="none" style={{ opacity: active ? 1 : 0.3 }}>
        {active && sortDir === 'asc'
          ? <path d="M5 2L8 7H2L5 2Z" fill="currentColor" />
          : <path d="M5 8L2 3H8L5 8Z" fill="currentColor" />}
      </svg>
    </div>
  );
}

const colGrid = '1fr 90px 150px';

export function CategoryBreakdown({ canExport, teamId }: { canExport: boolean; teamId?: string }) {
  const [rows, setRows] = useState<CategoryRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [startDate, setStartDate] = useState(daysAgo(30));
  const [endDate, setEndDate] = useState(new Date().toISOString().split('T')[0]);
  const [locationId, setLocationId] = useState('');
  const [sortKey, setSortKey] = useState<SortKey>('ticket_count');
  const [sortDir, setSortDir] = useState<SortDir>('desc');
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());

  const load = async () => {
    setLoading(true);
    setExpandedIds(new Set());
    try {
      const params: Record<string, string> = { start_date: startDate, end_date: endDate, limit: '200' };
      if (locationId) params.location_id = locationId;
      if (teamId) params.team_id = teamId;
      const data = await api.getCategoryBreakdown(params);
      setRows((data.rows || []).filter((r: CategoryRow) => r.ticket_count > 0));
    } catch { /* empty */ }
    setLoading(false);
  };

  useEffect(() => { load(); }, [startDate, endDate, locationId, teamId]);

  const handleSort = (k: SortKey) => {
    if (sortKey === k) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortKey(k); setSortDir('desc'); }
  };

  const toggle = useCallback((id: number) => setExpandedIds(prev => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  }), []);

  const tree = useMemo(() => buildTree(rows, sortKey, sortDir), [rows, sortKey, sortDir]);
  const flatRows = useMemo(() => flattenVisible(tree, expandedIds), [tree, expandedIds]);

  const totalTickets = rows.reduce((s, r) => s + (r.ticket_count || 0), 0);

  if (loading) return <div className="audit-empty">Loading category breakdown...</div>;

  return (
    <div>
      <div className="report-controls">
        <DateRangePicker startDate={startDate} endDate={endDate} onChange={(s, e) => { setStartDate(s); setEndDate(e); }} />
        <LocationFilter value={locationId} onChange={setLocationId} />
        {canExport && (
          <button className="report-csv-btn" style={{ marginLeft: 'auto' }}
            onClick={() => api.exportReportCsv('category-breakdown', { start_date: startDate, end_date: endDate })}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3" />
            </svg>
            CSV
          </button>
        )}
      </div>

      {flatRows.length === 0 ? (
        <div className="audit-empty">No tickets with categories in this period.</div>
      ) : (
        <div className="report-table">
          {/* Header */}
          <div className="report-table-header" style={{ display: 'grid', gridTemplateColumns: colGrid }}>
            <SortHeader label="Category" col="category_name" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} align="left" />
            <SortHeader label="Tickets" col="ticket_count" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
            <SortHeader label="Avg Resolution" col="avg_resolution_hours" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
          </div>

          {flatRows.map((row, i) => (
            <div key={i} className="report-table-row" style={{ display: 'grid', gridTemplateColumns: colGrid }}>
              <div
                className="report-table-cell"
                style={{ paddingLeft: row.depth * 20, display: 'flex', alignItems: 'center', gap: 4, cursor: row.children.length > 0 ? 'pointer' : undefined }}
                onClick={row.children.length > 0 ? () => toggle(row.category_id) : undefined}
              >
                {row.children.length > 0 ? (
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
                    style={{ flexShrink: 0, color: 'var(--t-text-dim)', transition: 'transform 0.15s', transform: expandedIds.has(row.category_id) ? 'rotate(0deg)' : 'rotate(-90deg)' }}>
                    <path d="M6 9l6 6 6-6" />
                  </svg>
                ) : row.depth > 0 ? (
                  <span style={{ color: 'var(--t-text-dim)', fontSize: 11, flexShrink: 0 }}>└</span>
                ) : null}
                <span style={{ fontWeight: row.depth === 0 ? 500 : 400, color: row.depth === 0 ? 'var(--t-text-bright)' : undefined, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {row.category_name}
                </span>
                {row.children.length > 0 && (
                  <span style={{ fontSize: 10, color: 'var(--t-text-dim)', flexShrink: 0 }}>({row.children.length})</span>
                )}
              </div>
              <div className="report-table-cell right" style={{ fontWeight: row.depth === 0 ? 600 : 400 }}>
                {row.ticket_count.toLocaleString()}
              </div>
              <div className="report-table-cell right" style={{ color: 'var(--t-text-muted)' }}>
                {row.avg_resolution_hours != null ? `${Number(row.avg_resolution_hours).toFixed(1)}h` : '—'}
              </div>
            </div>
          ))}

          {/* Summary */}
          <div className="report-table-summary" style={{ display: 'grid', gridTemplateColumns: colGrid }}>
            <div className="report-table-cell">Total ({flatRows.length} categories)</div>
            <div className="report-table-cell right">{totalTickets.toLocaleString()}</div>
            <div className="report-table-cell right" />
          </div>
        </div>
      )}
    </div>
  );
}
