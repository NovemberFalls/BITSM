import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import { pushUrl, buildUrl } from '../../utils/url';
import { TicketVolume } from './TicketVolume';
import { StatusBreakdown } from './StatusBreakdown';
import { CategoryBreakdown } from './CategoryBreakdown';
import { AgingTickets } from './AgingTickets';
import { SlaCompliance } from './SlaCompliance';
import { AgentPerformance } from './AgentPerformance';
import { AiEffectiveness } from './AiEffectiveness';
import { RoutingInsights } from './RoutingInsights';
import { LocationBreakdown } from './LocationBreakdown';

type ReportId =
  | 'ticket-volume' | 'status-breakdown' | 'category-breakdown' | 'aging-tickets'
  | 'sla-compliance' | 'agent-performance' | 'ai-effectiveness' | 'routing-insights'
  | 'location-breakdown';

interface ReportDef {
  id: ReportId;
  label: string;
  description: string;
  tier: 'free' | 'paid';
  icon: string;
}

const REPORT_DEFS: ReportDef[] = [
  { id: 'ticket-volume', label: 'Ticket Volume', description: 'Tickets created over time by status', tier: 'free', icon: 'M3 3v18h18M7 16l4-4 4 4 4-8' },
  { id: 'status-breakdown', label: 'Status Breakdown', description: 'Ticket counts by status and priority for a date range', tier: 'free', icon: 'M4 4h16v16H4zM4 10h16M10 4v16' },
  { id: 'category-breakdown', label: 'Category Breakdown', description: 'Top categories by ticket count and resolution time', tier: 'free', icon: 'M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2' },
  { id: 'aging-tickets', label: 'Aging Tickets', description: 'Open tickets by idle time — find stalled work before it becomes a problem', tier: 'free', icon: 'M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z' },
  { id: 'sla-compliance', label: 'SLA Compliance', description: 'Breach rates, response and resolution times by priority', tier: 'paid', icon: 'M9 12l2 2 4-4M7.835 4.697a3.42 3.42 0 001.946-.806 3.42 3.42 0 014.438 0 3.42 3.42 0 001.946.806 3.42 3.42 0 013.138 3.138 3.42 3.42 0 00.806 1.946 3.42 3.42 0 010 4.438 3.42 3.42 0 00-.806 1.946 3.42 3.42 0 01-3.138 3.138 3.42 3.42 0 00-1.946.806 3.42 3.42 0 01-4.438 0 3.42 3.42 0 00-1.946-.806 3.42 3.42 0 01-3.138-3.138 3.42 3.42 0 00-.806-1.946 3.42 3.42 0 010-4.438 3.42 3.42 0 00.806-1.946 3.42 3.42 0 013.138-3.138z' },
  { id: 'agent-performance', label: 'Agent Performance', description: 'Per-agent metrics: tickets, FCR, effort, AI vs human resolution', tier: 'paid', icon: 'M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z' },
  { id: 'ai-effectiveness', label: 'AI Effectiveness', description: 'Atlas resolution rate, L1/L2 split, escalations, cost per ticket', tier: 'paid', icon: 'M9.75 3.104v5.714a2.25 2.25 0 01-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 014.5 0m0 0v5.714a2.25 2.25 0 00.659 1.591L19 14.5M14.25 3.104c.251.023.501.05.75.082M5 14.5l-1.43 1.43a2.25 2.25 0 001.59 3.84h13.68a2.25 2.25 0 001.59-3.84L19 14.5' },
  { id: 'location-breakdown', label: 'Location Breakdown', description: 'Ticket volume, resolution, and SLA compliance by location', tier: 'paid', icon: 'M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z M15 11a3 3 0 11-6 0 3 3 0 016 0z' },
  { id: 'routing-insights', label: 'Routing Insights', description: 'Category coverage gaps and agent specializations', tier: 'paid', icon: 'M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7' },
];

interface ReportConfig {
  plan_tier: string;
  reports: Array<{ id: string; tier: string; accessible: boolean }>;
}

// Read initial report from the URL path (e.g. /reports/ticket-volume)
function reportFromPath(): ReportId | null {
  const parts = window.location.pathname.split('/');
  const idx = parts.indexOf('reports');
  if (idx >= 0 && parts[idx + 1]) {
    const candidate = parts[idx + 1] as ReportId;
    if (REPORT_DEFS.find(r => r.id === candidate)) return candidate;
  }
  // Also check server-injected initial_report from app_config
  try {
    const cfg = (window as any).__APP_CONFIG__;
    if (cfg?.initial_report && REPORT_DEFS.find(r => r.id === cfg.initial_report)) {
      return cfg.initial_report as ReportId;
    }
  } catch { /* empty */ }
  return null;
}

interface Team {
  id: number;
  name: string;
}

export function Reports() {
  const [activeReport, setActiveReport] = useState<ReportId | null>(reportFromPath);
  const [config, setConfig] = useState<ReportConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [exportingRaw, setExportingRaw] = useState(false);
  const [teams, setTeams] = useState<Team[]>([]);
  const [teamId, setTeamId] = useState('');

  useEffect(() => {
    (async () => {
      try {
        const [cfg, teamList] = await Promise.all([
          api.getReportConfig(),
          api.listTeams().catch(() => []),
        ]);
        setConfig(cfg);
        setTeams(teamList || []);
      } catch { /* empty */ }
      setLoading(false);
    })();
  }, []);

  // Sync URL when active report changes
  useEffect(() => {
    const target = activeReport ? `/reports/${activeReport}` : '/reports';
    const fullTarget = buildUrl(target);
    if (window.location.pathname !== fullTarget) {
      pushUrl(target, undefined, { report: activeReport });
    }
  }, [activeReport]);

  // Handle browser back/forward
  useEffect(() => {
    const onPop = (e: PopStateEvent) => {
      setActiveReport((e.state?.report as ReportId) ?? reportFromPath());
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const isAccessible = (reportId: string): boolean => {
    if (!config) return false;
    const r = config.reports.find(x => x.id === reportId);
    return r?.accessible ?? false;
  };

  const isPaidTier = config ? config.plan_tier !== 'free' : false;

  const handleCardClick = (report: ReportDef) => {
    if (isAccessible(report.id)) {
      setActiveReport(report.id);
      setTeamId('');
    }
  };

  const handleExportRaw = async () => {
    setExportingRaw(true);
    try {
      await api.exportTicketsCsv({});
    } catch { /* empty */ }
    setExportingRaw(false);
  };

  if (loading) {
    return (
      <div className="reports-view">
        <div className="reports-header"><h2 className="reports-title">Reports</h2></div>
        <div className="audit-empty">Loading reports...</div>
      </div>
    );
  }

  const freeReports = REPORT_DEFS.filter(r => r.tier === 'free');
  const paidReports = REPORT_DEFS.filter(r => r.tier === 'paid');

  // Landing page
  if (activeReport === null) {
    return (
      <div className="reports-view">
        <div className="reports-header">
          <h2 className="reports-title">Reports</h2>
          {isPaidTier && (
            <button
              className="btn btn-ghost"
              style={{ fontSize: 12, gap: 6 }}
              onClick={handleExportRaw}
              disabled={exportingRaw}
              title="Export all tickets with full detail (created_at, Atlas engaged, SLA, FCR, effort, resolution type)"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3" />
              </svg>
              {exportingRaw ? 'Exporting…' : 'Export All Tickets (CSV)'}
            </button>
          )}
        </div>
        <div className="reports-body">
          <div className="reports-landing">
            <div className="reports-card-section">
              <h3 className="reports-card-section-title">Basic Reports</h3>
              <div className="reports-card-grid">
                {freeReports.map(r => (
                  <button key={r.id} className="report-card" onClick={() => handleCardClick(r)}>
                    <div className="report-card-icon">
                      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                        <path d={r.icon} />
                      </svg>
                    </div>
                    <div className="report-card-content">
                      <div className="report-card-title">{r.label}</div>
                      <div className="report-card-desc">{r.description}</div>
                    </div>
                  </button>
                ))}
              </div>
            </div>

            <div className="reports-card-section">
              <h3 className="reports-card-section-title">
                Advanced Reports
                {!isPaidTier && <span className="tier-badge" style={{ marginLeft: 8 }}>Paid Plan</span>}
              </h3>
              <div className="reports-card-grid">
                {paidReports.map(r => {
                  const accessible = isAccessible(r.id);
                  return (
                    <button
                      key={r.id}
                      className={`report-card ${!accessible ? 'report-card-locked' : ''}`}
                      onClick={() => handleCardClick(r)}
                      disabled={!accessible}
                    >
                      <div className="report-card-icon">
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                          <path d={r.icon} />
                        </svg>
                      </div>
                      <div className="report-card-content">
                        <div className="report-card-title">
                          {r.label}
                          {!accessible && (
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" style={{ marginLeft: 6, opacity: 0.4 }}>
                              <path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zM9 8V6c0-1.66 1.34-3 3-3s3 1.34 3 3v2H9z" />
                            </svg>
                          )}
                        </div>
                        <div className="report-card-desc">{r.description}</div>
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  const currentDef = REPORT_DEFS.find(r => r.id === activeReport)!;

  return (
    <div className="reports-view">
      <div className="reports-header">
        <h2 className="reports-title">
          <button className="reports-back-btn" onClick={() => setActiveReport(null)} title="Back to Reports">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M19 12H5M12 19l-7-7 7-7" />
            </svg>
          </button>
          {currentDef.label}
        </h2>
        <div className="reports-nav">
          <span className="reports-nav-label">Basic</span>
          {freeReports.map(r => (
            <button
              key={r.id}
              className={`reports-nav-btn ${activeReport === r.id ? 'active' : ''}`}
              onClick={() => handleCardClick(r)}
            >
              {r.label}
            </button>
          ))}

          <span className="reports-nav-divider" />
          <span className="reports-nav-label">Advanced</span>

          {paidReports.map(r => {
            const accessible = isAccessible(r.id);
            return (
              <button
                key={r.id}
                className={`reports-nav-btn ${activeReport === r.id ? 'active' : ''} ${!accessible ? 'locked' : ''}`}
                onClick={() => handleCardClick(r)}
                disabled={!accessible}
                title={!accessible ? 'Requires a paid plan' : undefined}
              >
                {r.label}
                {!accessible && <span className="tier-badge">Paid</span>}
              </button>
            );
          })}
        </div>
      </div>

      {teams.length > 0 && (
        <div className="reports-team-bar">
          <select
            className="report-filter-select"
            value={teamId}
            onChange={e => setTeamId(e.target.value)}
            title="Filter by team"
          >
            <option value="">All Teams</option>
            {teams.map(t => (
              <option key={t.id} value={String(t.id)}>{t.name}</option>
            ))}
          </select>
        </div>
      )}

      <div className="reports-body">
        {activeReport === 'ticket-volume' && <TicketVolume canExport={isPaidTier} teamId={teamId} />}
        {activeReport === 'status-breakdown' && <StatusBreakdown canExport={isPaidTier} teamId={teamId} />}
        {activeReport === 'category-breakdown' && <CategoryBreakdown canExport={isPaidTier} teamId={teamId} />}
        {activeReport === 'aging-tickets' && <AgingTickets canExport={isPaidTier} />}
        {activeReport === 'sla-compliance' && <SlaCompliance canExport={isPaidTier} teamId={teamId} />}
        {activeReport === 'agent-performance' && <AgentPerformance canExport={isPaidTier} teamId={teamId} />}
        {activeReport === 'ai-effectiveness' && <AiEffectiveness canExport={isPaidTier} />}
        {activeReport === 'routing-insights' && <RoutingInsights />}
        {activeReport === 'location-breakdown' && <LocationBreakdown canExport={isPaidTier} teamId={teamId} />}
      </div>
    </div>
  );
}
