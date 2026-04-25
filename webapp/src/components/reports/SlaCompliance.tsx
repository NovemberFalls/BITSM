import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import { DateRangePicker } from './DateRangePicker';
import { ReportTable } from './ReportTable';
import type { ReportColumn } from './ReportTable';

interface SlaPerf {
  priority: string;
  total: number;
  breached: number;
  breach_rate: number | null;
  avg_first_response_minutes: number | null;
  avg_resolution_minutes: number | null;
}

interface SlaPolicy {
  priority: string;
  first_response_minutes: number;
  resolution_minutes: number;
}

const PRIORITY_LABELS: Record<string, string> = { p1: 'P1 — Urgent', p2: 'P2 — High', p3: 'P3 — Medium', p4: 'P4 — Low' };

const PERF_COLUMNS: ReportColumn[] = [
  { key: 'priority', label: 'Priority', width: '1fr' },
  { key: 'total', label: 'Total', width: '72px', align: 'right' },
  { key: 'breached', label: 'Breached', width: '85px', align: 'right' },
  { key: 'breach_rate', label: 'Breach %', width: '80px', align: 'right' },
  { key: 'avg_first_response_minutes', label: 'Avg 1st Resp', width: '110px', align: 'right' },
  { key: 'avg_resolution_minutes', label: 'Avg Resolve', width: '105px', align: 'right' },
];

const POLICY_COLUMNS: ReportColumn[] = [
  { key: 'priority', label: 'Priority', width: '1fr' },
  { key: 'first_response_minutes', label: '1st Resp Target', width: '140px', align: 'right' },
  { key: 'resolution_minutes', label: 'Resolve Target', width: '130px', align: 'right' },
];

function daysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().split('T')[0];
}

function breachColor(rate: number | null): string {
  if (rate == null) return '';
  if (rate < 5) return 'sla-good';
  if (rate <= 15) return 'sla-warn';
  return 'sla-bad';
}

function formatMinutes(mins: number | null): string {
  if (mins == null) return '—';
  if (mins < 60) return `${Math.round(mins)}m`;
  const h = Math.floor(mins / 60);
  const m = Math.round(mins % 60);
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

export function SlaCompliance({ canExport, teamId }: { canExport: boolean; teamId?: string }) {
  const [perf, setPerf] = useState<SlaPerf[]>([]);
  const [policies, setPolicies] = useState<SlaPolicy[]>([]);
  const [loading, setLoading] = useState(true);
  const [startDate, setStartDate] = useState(daysAgo(30));
  const [endDate, setEndDate] = useState(new Date().toISOString().split('T')[0]);

  const load = async () => {
    setLoading(true);
    try {
      const params: Record<string, string> = { start_date: startDate, end_date: endDate };
      if (teamId) params.team_id = teamId;
      const data = await api.getSlaCompliance(params);
      setPerf(data.performance || []);
      setPolicies(data.policies || []);
    } catch { /* empty */ }
    setLoading(false);
  };

  useEffect(() => { load(); }, [startDate, endDate, teamId]);

  if (loading) return <div className="audit-empty">Loading SLA compliance...</div>;

  return (
    <div>
      <div className="report-controls">
        <DateRangePicker startDate={startDate} endDate={endDate} onChange={(s, e) => { setStartDate(s); setEndDate(e); }} />
      </div>

      {policies.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <h4 className="report-section-label">SLA Targets</h4>
          <ReportTable
            columns={POLICY_COLUMNS}
            rows={policies.map(p => ({
              ...p,
              priority: PRIORITY_LABELS[p.priority] || p.priority,
            }))}
          />
        </div>
      )}

      <h4 className="report-section-label">Actual Performance</h4>
      <ReportTable
        columns={PERF_COLUMNS}
        rows={perf.map(p => ({
          ...p,
          priority: PRIORITY_LABELS[p.priority] || p.priority,
        }))}
        formatCell={(key, value) => {
          if (key === 'breach_rate' && value != null) {
            return <span className={breachColor(value)}>{value}%</span>;
          }
          if ((key === 'avg_first_response_minutes' || key === 'avg_resolution_minutes') && value != null) {
            return formatMinutes(value);
          }
          return undefined;
        }}
        onExportCsv={canExport ? () => api.exportReportCsv('sla-compliance', { start_date: startDate, end_date: endDate }) : undefined}
      />
    </div>
  );
}
