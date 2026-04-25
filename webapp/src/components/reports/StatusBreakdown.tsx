import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import { ReportTable } from './ReportTable';
import type { ReportColumn } from './ReportTable';

interface StatusRow {
  status: string;
  priority: string;
  count: number;
}

const STATUS_LABELS: Record<string, string> = {
  open: 'Open',
  pending: 'Pending',
  resolved: 'Resolved',
  closed_not_resolved: 'Closed (NR)',
};

const PRIORITY_ORDER = ['p1', 'p2', 'p3', 'p4'];

const COLUMNS: ReportColumn[] = [
  { key: 'status', label: 'Status', width: '1fr' },
  { key: 'p1', label: 'P1', width: '70px', align: 'right' },
  { key: 'p2', label: 'P2', width: '70px', align: 'right' },
  { key: 'p3', label: 'P3', width: '70px', align: 'right' },
  { key: 'p4', label: 'P4', width: '70px', align: 'right' },
  { key: 'total', label: 'Total', width: '80px', align: 'right' },
];

export function StatusBreakdown({ canExport, teamId }: { canExport: boolean; teamId?: string }) {
  const [rows, setRows] = useState<StatusRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const params: Record<string, string> = {};
        if (teamId) params.team_id = teamId;
        const data = await api.getStatusBreakdown(Object.keys(params).length ? params : undefined);
        setRows(data.rows || []);
      } catch { /* empty */ }
      setLoading(false);
    })();
  }, [teamId]);

  if (loading) return <div className="audit-empty">Loading status breakdown...</div>;

  // Pivot: rows from (status, priority, count) → matrix rows
  const statusOrder = ['open', 'pending', 'resolved', 'closed_not_resolved'];
  const matrix = statusOrder.map(status => {
    const entry: Record<string, any> = { status: STATUS_LABELS[status] || status };
    let total = 0;
    for (const p of PRIORITY_ORDER) {
      const match = rows.find(r => r.status === status && r.priority === p);
      const val = match?.count || 0;
      entry[p] = val;
      total += val;
    }
    entry.total = total;
    return entry;
  });

  const grandTotal: Record<string, any> = { status: 'Total' };
  let gt = 0;
  for (const p of PRIORITY_ORDER) {
    const val = matrix.reduce((s, r) => s + (r[p] || 0), 0);
    grandTotal[p] = val;
    gt += val;
  }
  grandTotal.total = gt;

  return (
    <div>
      <div className="report-controls">
        <span style={{ fontSize: 12, color: 'var(--t-text-muted)' }}>Current snapshot — all tickets</span>
      </div>
      <ReportTable
        columns={COLUMNS}
        rows={matrix}
        summaryRow={grandTotal}
        onExportCsv={canExport ? () => api.exportReportCsv('status-breakdown', {}) : undefined}
      />
    </div>
  );
}
