import { useEffect, useMemo, useCallback, useState } from 'react';
import { api } from '../../api/client';
import { DateRangePicker } from './DateRangePicker';

interface CategoryCoverage {
  category_id: number;
  category_name: string;
  parent_id: number | null;
  agents_with_experience: number;
  total_resolved: number;
  open_tickets: number;
}

interface AgentSpec {
  agent_id: number;
  agent_name: string;
  total_resolved: number;
  open_tickets: number;
  avg_effort: number;
  top_categories: Array<{ category: string; count: number }>;
}

interface RoutingData {
  category_coverage: CategoryCoverage[];
  agent_specializations: AgentSpec[];
  coverage_gaps: CategoryCoverage[];
}

interface CategoryNode extends CategoryCoverage {
  children: CategoryNode[];
  depth: number;
  coverage: 'none' | 'thin' | 'moderate' | 'strong';
}

type TabView = 'categories' | 'agents';

function daysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().split('T')[0];
}

function getCoverage(n: number): 'none' | 'thin' | 'moderate' | 'strong' {
  if (n === 0) return 'none';
  if (n === 1) return 'thin';
  if (n <= 3) return 'moderate';
  return 'strong';
}

const COVERAGE_COLOR: Record<string, string> = {
  none: 'var(--t-error)',
  thin: 'var(--t-warning)',
  moderate: 'var(--t-text-muted)',
  strong: 'var(--t-success)',
};
const COVERAGE_LABEL: Record<string, string> = {
  none: 'None', thin: 'Thin', moderate: 'OK', strong: 'Strong',
};

function buildCategoryTree(cats: CategoryCoverage[]): CategoryNode[] {
  const byParent: Record<string, CategoryCoverage[]> = {};
  for (const c of cats) {
    const key = c.parent_id == null ? '__root__' : String(c.parent_id);
    (byParent[key] = byParent[key] || []).push(c);
  }

  function walk(parentKey: string, depth: number): CategoryNode[] {
    return (byParent[parentKey] || []).sort((a, b) => a.category_name.localeCompare(b.category_name)).map(c => ({
      ...c,
      depth,
      coverage: getCoverage(c.agents_with_experience),
      children: walk(String(c.category_id), depth + 1),
    }));
  }
  return walk('__root__', 0);
}

function flattenVisible(nodes: CategoryNode[], expanded: Set<number>): CategoryNode[] {
  const out: CategoryNode[] = [];
  function walk(list: CategoryNode[]) {
    for (const n of list) {
      out.push(n);
      if (n.children.length > 0 && expanded.has(n.category_id)) walk(n.children);
    }
  }
  walk(nodes);
  return out;
}

const colGrid = '1fr 90px 90px 72px 90px';

export function RoutingInsights() {
  const [data, setData] = useState<RoutingData | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<TabView>('categories');
  const [startDate, setStartDate] = useState(daysAgo(90));
  const [endDate, setEndDate] = useState(new Date().toISOString().split('T')[0]);
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());

  const load = async () => {
    setLoading(true);
    setExpandedIds(new Set());
    try {
      const d = await api.getRoutingInsights({ start_date: startDate, end_date: endDate });
      setData(d);
    } catch { /* empty */ }
    setLoading(false);
  };

  useEffect(() => { load(); }, [startDate, endDate]);

  const toggle = useCallback((id: number) => setExpandedIds(prev => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  }), []);

  const tree = useMemo(() => {
    if (!data?.category_coverage) return [];
    return buildCategoryTree(data.category_coverage);
  }, [data]);

  const categoryRows = useMemo(() => flattenVisible(tree, expandedIds), [tree, expandedIds]);

  const agentRows = (data?.agent_specializations || [])
    .filter(a => a.total_resolved > 0 || a.open_tickets > 0)
    .sort((a, b) => b.total_resolved - a.total_resolved);

  const gapCount = (data?.coverage_gaps || []).length;

  if (loading) return <div className="audit-empty">Loading routing insights...</div>;
  if (!data) return <div className="audit-empty">No routing data available.</div>;

  return (
    <div>
      {/* Help banner */}
      <div style={{ marginBottom: 16, padding: '12px 16px', background: 'var(--t-panel-alt)', borderRadius: 'var(--radius-xs)', border: '1px solid var(--t-border)', fontSize: 12, color: 'var(--t-text-muted)', lineHeight: 1.6 }}>
        <strong style={{ color: 'var(--t-text)', display: 'block', marginBottom: 4 }}>How to use Routing Insights</strong>
        <strong>Categories</strong> — Shows which problem categories are well-covered by experienced agents.{' '}
        <span style={{ color: 'var(--t-error)' }}>None</span> = no agent has resolved a ticket in this category (routing risk).{' '}
        <span style={{ color: 'var(--t-warning)' }}>Thin</span> = only one agent has experience (bus factor risk).{' '}
        Use this to spot training gaps and re-route tickets to the right specialist.{' '}
        <strong>Agents</strong> — Shows each agent's resolved workload and their top categories, helping you assign incoming tickets to the best-fit agent.
      </div>

      <div className="report-controls">
        <DateRangePicker startDate={startDate} endDate={endDate} onChange={(s, e) => { setStartDate(s); setEndDate(e); }} />
        <div className="report-group-toggle">
          <button className={`report-group-btn ${tab === 'categories' ? 'active' : ''}`} onClick={() => setTab('categories')}>
            Categories ({categoryRows.length})
          </button>
          <button className={`report-group-btn ${tab === 'agents' ? 'active' : ''}`} onClick={() => setTab('agents')}>
            Agents ({agentRows.length})
          </button>
        </div>
        {tab === 'categories' && gapCount > 0 && (
          <span style={{ fontSize: 12, color: 'var(--t-error)' }}>
            {gapCount} categories with thin or no coverage
          </span>
        )}
      </div>

      {tab === 'categories' && (
        categoryRows.length === 0 ? (
          <div className="audit-empty">No categories configured.</div>
        ) : (
          <div className="report-table">
            <div className="report-table-header" style={{ gridTemplateColumns: colGrid }}>
              <div className="report-table-cell">Category</div>
              <div className="report-table-cell right">Specialists</div>
              <div className="report-table-cell right">Resolved</div>
              <div className="report-table-cell right">Open</div>
              <div className="report-table-cell center">Coverage</div>
            </div>

            {categoryRows.map((row, i) => (
              <div key={i} className="report-table-row" style={{ gridTemplateColumns: colGrid }}>
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
                <div className="report-table-cell right" style={{ color: 'var(--t-text-muted)' }}>
                  {row.agents_with_experience || '—'}
                </div>
                <div className="report-table-cell right">{row.total_resolved || '—'}</div>
                <div className="report-table-cell right" style={{ color: row.open_tickets > 0 ? 'var(--t-warning)' : undefined }}>
                  {row.open_tickets || '—'}
                </div>
                <div className="report-table-cell center">
                  <span style={{ color: COVERAGE_COLOR[row.coverage], fontWeight: 600, fontSize: 11 }}>
                    {COVERAGE_LABEL[row.coverage]}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )
      )}

      {tab === 'agents' && (
        agentRows.length === 0 ? (
          <div className="audit-empty">No agents with ticket activity in this period.</div>
        ) : (
          <div className="report-table">
            <div className="report-table-header" style={{ gridTemplateColumns: '1fr 90px 72px 80px 1fr' }}>
              <div className="report-table-cell">Agent</div>
              <div className="report-table-cell right">Resolved</div>
              <div className="report-table-cell right">Open</div>
              <div className="report-table-cell right">Avg Effort</div>
              <div className="report-table-cell">Top Categories</div>
            </div>
            {agentRows.map((a, i) => (
              <div key={i} className="report-table-row" style={{ gridTemplateColumns: '1fr 90px 72px 80px 1fr' }}>
                <div className="report-table-cell" style={{ fontWeight: 500 }}>{a.agent_name}</div>
                <div className="report-table-cell right">{a.total_resolved || '—'}</div>
                <div className="report-table-cell right" style={{ color: a.open_tickets > 0 ? 'var(--t-warning)' : undefined }}>
                  {a.open_tickets || '—'}
                </div>
                <div className="report-table-cell right">
                  {a.avg_effort > 0 ? (
                    <span className={Number(a.avg_effort) >= 4 ? 'sla-bad' : Number(a.avg_effort) >= 3 ? 'sla-warn' : 'sla-good'}>
                      {Number(a.avg_effort).toFixed(1)}
                    </span>
                  ) : '—'}
                </div>
                <div className="report-table-cell" style={{ color: 'var(--t-text-muted)', fontSize: 12 }}>
                  {a.top_categories.length > 0
                    ? a.top_categories.map(c => `${c.category} (${c.count})`).join(' · ')
                    : '—'}
                </div>
              </div>
            ))}
          </div>
        )
      )}
    </div>
  );
}
