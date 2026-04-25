import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import { DateRangePicker } from './DateRangePicker';
import { ReportTable } from './ReportTable';
import type { ReportColumn } from './ReportTable';

interface AiSummary {
  total_engagements: number;
  ai_resolved: number;
  ai_resolution_rate: number | null;
  l1_count: number;
  l2_count: number;
  human_takeover_count: number;
  escalation_rate: number | null;
  avg_turns_before_resolve: number | null;
}

interface CostData {
  avg_cost_per_ticket: number | null;
  tickets_with_ai_cost: number | null;
  total_cost?: number | null;  // super_admin only
}

const SUMMARY_COLUMNS: ReportColumn[] = [
  { key: 'label', label: 'Metric', width: '1fr' },
  { key: 'value', label: 'Value', width: '120px', align: 'right' },
];

function daysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().split('T')[0];
}

export function AiEffectiveness({ canExport }: { canExport: boolean }) {
  const [summary, setSummary] = useState<AiSummary | null>(null);
  const [cost, setCost] = useState<CostData | null>(null);
  const [loading, setLoading] = useState(true);
  const [startDate, setStartDate] = useState(daysAgo(30));
  const [endDate, setEndDate] = useState(new Date().toISOString().split('T')[0]);

  const load = async () => {
    setLoading(true);
    try {
      const data = await api.getAiEffectiveness({ start_date: startDate, end_date: endDate });
      setSummary(data.summary || null);
      setCost(data.cost || null);
    } catch { /* empty */ }
    setLoading(false);
  };

  useEffect(() => { load(); }, [startDate, endDate]);

  if (loading) return <div className="audit-empty">Loading AI effectiveness...</div>;
  if (!summary || summary.total_engagements === 0) return (
    <div className="audit-empty" style={{ maxWidth: 480, margin: '48px auto', textAlign: 'center' }}>
      <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 8 }}>No AI engagement data yet</div>
      <div style={{ fontSize: 13, color: 'var(--t-text-muted)', lineHeight: 1.6 }}>
        Atlas must auto-engage on incoming tickets to populate this report.<br />
        Go to <strong>Admin → Tenants → AI Features</strong> and enable <strong>AI Ticket Review</strong>{' '}
        to allow Atlas to automatically analyze and respond to new tickets.
      </div>
    </div>
  );

  const kpis = [
    { label: 'AI Resolution Rate', value: summary.ai_resolution_rate != null ? `${summary.ai_resolution_rate}%` : '—' },
    { label: 'Escalation Rate', value: summary.escalation_rate != null ? `${summary.escalation_rate}%` : '—' },
    { label: 'Avg Turns to Resolve', value: summary.avg_turns_before_resolve != null ? summary.avg_turns_before_resolve.toFixed(1) : '—' },
  ];

  const breakdownRows = [
    { label: 'Total Engagements', value: summary.total_engagements },
    { label: 'AI Resolved', value: summary.ai_resolved },
    { label: 'L1 (Haiku) Engagements', value: summary.l1_count },
    { label: 'L2 (Sonnet) Escalations', value: summary.l2_count },
    { label: 'Human Takeovers', value: summary.human_takeover_count },
  ];

  const costRows: { label: string; value: string }[] = [];
  if (cost?.avg_cost_per_ticket != null) {
    costRows.push({ label: 'Avg AI Cost per Ticket', value: `$${Number(cost.avg_cost_per_ticket).toFixed(4)}` });
  }
  if (cost?.tickets_with_ai_cost != null) {
    costRows.push({ label: 'Tickets with AI Cost', value: String(cost.tickets_with_ai_cost) });
  }
  if (cost?.total_cost != null) {
    costRows.push({ label: 'Total AI Cost', value: `$${Number(cost.total_cost).toFixed(2)}` });
  }

  return (
    <div>
      <div className="report-controls">
        <DateRangePicker startDate={startDate} endDate={endDate} onChange={(s, e) => { setStartDate(s); setEndDate(e); }} />
      </div>

      <div className="report-kpis">
        {kpis.map(k => (
          <div key={k.label} className="report-kpi">
            <div className="report-kpi-value">{k.value}</div>
            <div className="report-kpi-label">{k.label}</div>
          </div>
        ))}
      </div>

      <h4 className="report-section-label">Engagement Breakdown</h4>
      <ReportTable
        columns={SUMMARY_COLUMNS}
        rows={breakdownRows}
        onExportCsv={canExport ? () => api.exportReportCsv('ai-effectiveness', { start_date: startDate, end_date: endDate }) : undefined}
      />

      {costRows.length > 0 && (
        <>
          <h4 className="report-section-label" style={{ marginTop: 16 }}>Cost Analysis</h4>
          <ReportTable
            columns={SUMMARY_COLUMNS}
            rows={costRows}
          />
        </>
      )}
    </div>
  );
}
