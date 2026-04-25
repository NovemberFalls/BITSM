import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import { LocationFilter } from './LocationFilter';
import { ReportTable } from './ReportTable';
import type { ReportColumn } from './ReportTable';

interface Bucket {
  age_bucket: string;
  total: number;
  p1: number;
  p2: number;
  p3: number;
  p4: number;
}

interface StaleTicket {
  id: number;
  subject: string;
  priority: string;
  status: string;
  created_at: string;
  updated_at: string;
  assignee_name: string | null;
  location_name: string | null;
  category_name: string | null;
  days_stale: number;
}

const BUCKET_COLUMNS: ReportColumn[] = [
  { key: 'age_bucket', label: 'Last Updated', width: '140px' },
  { key: 'total', label: 'Total', width: '80px', align: 'right' },
  { key: 'p1', label: 'P1 Urgent', width: '90px', align: 'right' },
  { key: 'p2', label: 'P2 High', width: '80px', align: 'right' },
  { key: 'p3', label: 'P3 Med', width: '80px', align: 'right' },
  { key: 'p4', label: 'P4 Low', width: '80px', align: 'right' },
];

const STALE_COLUMNS: ReportColumn[] = [
  { key: 'id', label: '#', width: '60px' },
  { key: 'subject', label: 'Subject', width: '1fr' },
  { key: 'priority', label: 'Pri', width: '60px', align: 'center', sortable: true },
  { key: 'status', label: 'Status', width: '90px', sortable: true },
  { key: 'days_stale', label: 'Days Idle', width: '90px', align: 'right', sortable: true },
  { key: 'assignee_name', label: 'Assignee', width: '130px', sortable: true },
  { key: 'location_name', label: 'Location', width: '130px', sortable: true },
  { key: 'category_name', label: 'Category', width: '130px', sortable: true },
];

const PRIORITY_COLOR: Record<string, string> = {
  p1: 'var(--t-error)',
  p2: 'var(--t-warning)',
  p3: 'var(--t-accent-text)',
  p4: 'var(--t-success)',
};

const PRIORITY_LABEL: Record<string, string> = { p1: 'P1', p2: 'P2', p3: 'P3', p4: 'P4' };

export function AgingTickets({ canExport }: { canExport: boolean }) {
  const [buckets, setBuckets] = useState<Bucket[]>([]);
  const [stale, setStale] = useState<StaleTicket[]>([]);
  const [loading, setLoading] = useState(true);
  const [locationId, setLocationId] = useState('');
  const [priorityFilter, setPriorityFilter] = useState('');

  const load = async () => {
    setLoading(true);
    try {
      const params: Record<string, string> = {};
      if (locationId) params.location_id = locationId;
      if (priorityFilter) params.priority = priorityFilter;
      const data = await api.getAgingTickets(params);
      setBuckets(data.buckets || []);
      setStale(data.stale_tickets || []);
    } catch { /* empty */ }
    setLoading(false);
  };

  useEffect(() => { load(); }, [locationId, priorityFilter]);

  const totalStale = buckets.reduce((s, b) => s + (b.total || 0), 0);

  if (loading) return <div className="audit-empty">Loading aging tickets...</div>;

  return (
    <div>
      <div className="report-controls">
        <LocationFilter value={locationId} onChange={setLocationId} />
        <select
          className="report-filter-select"
          value={priorityFilter}
          onChange={e => setPriorityFilter(e.target.value)}
        >
          <option value="">All Priorities</option>
          <option value="p1">P1 — Urgent</option>
          <option value="p2">P2 — High</option>
          <option value="p3">P3 — Medium</option>
          <option value="p4">P4 — Low</option>
        </select>
      </div>

      {totalStale === 0 ? (
        <div className="audit-empty" style={{ marginTop: 32 }}>No open or pending tickets — inbox zero!</div>
      ) : (
        <>
          <div style={{ marginBottom: 24 }}>
            <div className="report-section-title">Age Summary</div>
            <ReportTable
              columns={BUCKET_COLUMNS}
              rows={buckets}
              formatCell={(key, value, row) => {
                if (key === 'p1' && value > 0) return <span style={{ color: 'var(--t-error)', fontWeight: 600 }}>{value}</span>;
                if (key === 'p2' && value > 0) return <span style={{ color: 'var(--t-warning)', fontWeight: 600 }}>{value}</span>;
                if (key === 'age_bucket') {
                  const bucket = row as Bucket;
                  const hasUrgent = (bucket.p1 || 0) > 0;
                  return <span style={{ color: hasUrgent ? 'var(--t-error)' : undefined, fontWeight: hasUrgent ? 600 : undefined }}>{value}</span>;
                }
                return undefined;
              }}
              onExportCsv={canExport ? () => api.exportReportCsv('aging-tickets', {}) : undefined}
            />
          </div>

          <div>
            <div className="report-section-title">Stale Tickets (idle 3+ days)</div>
            <ReportTable
              columns={STALE_COLUMNS}
              rows={stale}
              defaultSortKey="days_stale"
              defaultSortDir="desc"
              emptyMessage="No tickets idle for 3+ days."
              formatCell={(key, value, row) => {
                if (key === 'priority') {
                  return (
                    <span style={{ color: PRIORITY_COLOR[value] || 'inherit', fontWeight: 600, fontSize: 11 }}>
                      {PRIORITY_LABEL[value] || value}
                    </span>
                  );
                }
                if (key === 'days_stale') {
                  const days = Number(value);
                  const color = days >= 14 ? 'var(--t-error)' : days >= 7 ? 'var(--t-warning)' : 'var(--t-text-muted)';
                  return <span style={{ color, fontWeight: days >= 7 ? 600 : undefined }}>{days.toFixed(1)}d</span>;
                }
                if (key === 'status') {
                  return <span className={`badge badge-${value}`}>{value}</span>;
                }
                if (key === 'id') {
                  return <span style={{ color: 'var(--t-text-muted)', fontFamily: 'var(--mono)', fontSize: 11 }}>#{value}</span>;
                }
                return undefined;
              }}
            />
          </div>
        </>
      )}
    </div>
  );
}
