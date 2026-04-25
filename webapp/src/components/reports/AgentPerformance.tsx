import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import { DateRangePicker } from './DateRangePicker';
import { ReportTable } from './ReportTable';
import type { ReportColumn } from './ReportTable';

interface AgentRow {
  agent_id: number;
  agent_name: string;
  role: string;
  ticket_count: number;
  resolved_count: number;
  avg_resolution_hours: number | null;
  fcr_rate: number | null;
  avg_effort: number | null;
  ai_resolved: number;
  human_resolved: number;
  hybrid_resolved: number;
}

const ROLE_LABEL: Record<string, string> = {
  super_admin: 'Super Admin',
  tenant_admin: 'Admin',
  agent: 'Agent',
  ai: 'AI',
};

const COLUMNS: ReportColumn[] = [
  { key: 'agent_name', label: 'Agent', width: '1fr' },
  { key: 'ticket_count', label: 'Tickets', width: '80px', align: 'right', sortable: true },
  { key: 'resolved_count', label: 'Resolved', width: '90px', align: 'right', sortable: true },
  { key: 'avg_resolution_hours', label: 'Avg Res', width: '85px', align: 'right', sortable: true },
  { key: 'fcr_rate', label: 'FCR %', width: '72px', align: 'right', sortable: true },
  { key: 'avg_effort', label: 'Effort', width: '72px', align: 'right', sortable: true },
  { key: 'ai_resolved', label: 'AI Res', width: '65px', align: 'right' },
  { key: 'human_resolved', label: 'Human', width: '70px', align: 'right' },
  { key: 'hybrid_resolved', label: 'Hybrid', width: '70px', align: 'right' },
];

function daysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().split('T')[0];
}

export function AgentPerformance({ canExport, teamId }: { canExport: boolean; teamId?: string }) {
  const [rows, setRows] = useState<AgentRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [startDate, setStartDate] = useState(daysAgo(30));
  const [endDate, setEndDate] = useState(new Date().toISOString().split('T')[0]);

  const load = async () => {
    setLoading(true);
    try {
      const params: Record<string, string> = { start_date: startDate, end_date: endDate };
      if (teamId) params.team_id = teamId;
      const data = await api.getAgentPerformance(params);
      setRows(data.agents || []);
    } catch { /* empty */ }
    setLoading(false);
  };

  useEffect(() => { load(); }, [startDate, endDate, teamId]);

  if (loading) return <div className="audit-empty">Loading agent performance...</div>;

  return (
    <div>
      <div className="report-controls">
        <DateRangePicker startDate={startDate} endDate={endDate} onChange={(s, e) => { setStartDate(s); setEndDate(e); }} />
      </div>
      <ReportTable
        columns={COLUMNS}
        rows={rows}
        defaultSortKey="ticket_count"
        defaultSortDir="desc"
        emptyMessage="No agent data for this period."
        formatCell={(key, value, row) => {
          if (key === 'agent_name') {
            const r = row as AgentRow;
            const isAtlas = r.agent_id === -1;
            return (
              <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontWeight: isAtlas ? 600 : undefined, color: isAtlas ? 'var(--t-accent-text)' : undefined }}>
                  {value}
                </span>
                <span style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.6px', color: isAtlas ? 'var(--t-accent-text)' : 'var(--t-text-dim)', opacity: 0.8 }}>
                  {ROLE_LABEL[r.role] || r.role}
                </span>
              </span>
            );
          }
          if (key === 'avg_resolution_hours' && value != null) return `${Number(value).toFixed(1)}h`;
          if (key === 'fcr_rate' && value != null) return `${value}%`;
          if (key === 'avg_effort' && value != null) {
            const v = Number(value);
            const cls = v >= 4 ? 'sla-bad' : v >= 3 ? 'sla-warn' : 'sla-good';
            return <span className={cls}>{v.toFixed(1)}</span>;
          }
          return undefined;
        }}
        onExportCsv={canExport ? () => { const p: Record<string, string> = { start_date: startDate, end_date: endDate }; if (teamId) p.team_id = teamId; api.exportReportCsv('agent-performance', p); } : undefined}
      />
    </div>
  );
}
