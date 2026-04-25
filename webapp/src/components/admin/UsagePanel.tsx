import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { api } from '../../api/client';
import { useAuthStore } from '../../store/authStore';

// ─── Period config ───────────────────────────────────────────────────────────

type Period = 'day' | 'week' | 'month' | 'quarter' | 'year';

const PERIODS: { key: Period; label: string }[] = [
  { key: 'day',     label: 'Daily' },
  { key: 'week',    label: 'Weekly' },
  { key: 'month',   label: 'Monthly' },
  { key: 'quarter', label: 'Quarterly' },
  { key: 'year',    label: 'Annually' },
];

// ─── Caller metadata — canonical pipeline order with phase groupings ──────────

interface CallerMeta {
  key:     string;
  label:   string;
  phase:   string;
  tooltip: string;
}

/** Ordered pipeline steps. The UI always renders them in this order,
 *  even if a caller has 0 calls for the selected period. */
export const PIPELINE_STEPS: CallerMeta[] = [
  // ── Ticket Create Pipeline ─────────────────────────────────────────────────
  {
    key: 'tagging', label: 'Auto-Tag', phase: 'Ticket Create Pipeline',
    tooltip: 'Auto-Tag (Step 1) — Claude Haiku reads the ticket subject, description, and category then suggests 2–5 short lowercase tags. Runs in the first pipeline lane immediately after ticket creation.',
  },
  {
    key: 'enrichment', label: 'Enrichment', phase: 'Ticket Create Pipeline',
    tooltip: 'Enrichment (Step 2) — Converts the ticket text to a vector embedding, runs a similarity search across enabled knowledge modules, and uses Claude Haiku to summarise the most relevant articles into a structured context block. That block is passed to the Engage step so Atlas has KB grounding before it writes its triage note.',
  },
  {
    key: 'atlas.triage', label: 'Atlas Engage', phase: 'Ticket Create Pipeline',
    tooltip: 'Atlas Engage (Step 3) — Atlas (Claude Haiku) reads the enrichment context and full ticket, then posts an internal note with a category suggestion, priority assessment, recommended next steps, and a confidence score. Only fires when AI Ticket Review is enabled for the tenant.',
  },
  // ── In-Ticket AI ──────────────────────────────────────────────────────────
  {
    key: 'atlas.follow_up', label: 'In-Ticket Chat', phase: 'In-Ticket AI',
    tooltip: 'In-Ticket Chat — Atlas responds to agent messages inside the ticket\'s Atlas tab. Uses the same KB-grounded L1 loop but with full ticket comment history as additional context. Stays passive once a human agent is assigned unless directly addressed.',
  },
  {
    key: 'l1_chat', label: 'L1 Chat', phase: 'In-Ticket AI',
    tooltip: 'L1 Chat (Claude Haiku) — Real-time KB-grounded Q&A via the Atlas sidebar and customer chat widget. Runs a tool-use loop: searches enabled knowledge modules, retrieves ranked chunks, then composes a grounded response. ~$0.04 per turn.',
  },
  {
    key: 'l2_chat', label: 'L2 Escalation', phase: 'In-Ticket AI',
    tooltip: 'L2 Escalation (Claude Sonnet) — Triggered when L1 cannot resolve or the user explicitly escalates. One-shot consultant call with the full conversation history and KB context. Produces a detailed response with recommended actions. ~$0.20 per turn.',
  },
  {
    key: 'atlas.handoff', label: 'Handoff Summary', phase: 'In-Ticket AI',
    tooltip: 'Handoff Summary — When a ticket is reassigned, Atlas generates a 3-line summary for the new assignee covering: what has been tried, customer sentiment, and recommended next step.',
  },
  // ── Close Pipeline ────────────────────────────────────────────────────────
  {
    key: 'atlas.audit', label: 'Close Audit', phase: 'Close Pipeline',
    tooltip: 'Close Audit — On ticket resolve or close, Atlas scores the resolution quality (0–100) across axes: accuracy, completeness, professionalism, and KB alignment. Flags poor resolutions and queues them for manager review in the Audit panel.',
  },
  // ── KB Management ─────────────────────────────────────────────────────────
  {
    key: 'atlas.gaps', label: 'Gap Detection', phase: 'KB Management',
    tooltip: 'Gap Detection (weekly cron) — Scans open tickets that had no KB article match and clusters them by topic. Produces a ranked list of knowledge gaps — topics people ask about that have no documented answer — and writes them to the Knowledge Gaps audit queue.',
  },
  {
    key: 'doc_tagging', label: 'Document Tagging', phase: 'KB Management',
    tooltip: 'Document Tagging — When a KB article is ingested into the pipeline, Claude Haiku reads the content and assigns semantic tags. Used for filtering and similarity ranking during RAG retrieval.',
  },
  {
    key: 'kb_embed', label: 'Article Embedding', phase: 'KB Management',
    tooltip: 'Article Embedding — When a tenant uploads or creates a KB article, the content is chunked and embedded via Voyage AI for vector similarity search. Costs reflect embedding token usage only (no LLM output tokens).',
  },
  // ── Phone ─────────────────────────────────────────────────────────────────
  {
    key: 'phone.calls', label: 'AI Calls', phase: 'Phone',
    tooltip: 'AI Calls — Inbound calls that connected to an ElevenLabs agent (Atlas EN or Sofía ES). Includes ElevenLabs LLM token usage + Twilio per-minute charge. Token counts and cost fetched from EL after each call.',
  },
  {
    key: 'phone.ivr', label: 'IVR Abandoned', phase: 'Phone',
    tooltip: 'IVR Abandoned — Caller reached the bilingual IVR greeting ("Press 1 for English / Oprima 2 para español") but hung up before or after pressing a digit, without connecting to an agent. Twilio charges per minute; no LLM tokens consumed.',
  },
  {
    key: 'phone.dropped', label: 'Dropped', phase: 'Phone',
    tooltip: 'Dropped — Call connected to Twilio but never reached the IVR (e.g. very early hang-up, pre-IVR test calls, or routing failure). Twilio still charges per minute for the connected duration.',
  },
];

// Fast lookup by caller key
const STEP_BY_KEY: Record<string, CallerMeta> = Object.fromEntries(
  PIPELINE_STEPS.map(s => [s.key, s]),
);

// Short model display names
function shortModel(model: string | null | undefined): string {
  if (!model) return '—';
  if (model.includes('ElevenLabs'))  return model;
  if (model.includes('haiku'))       return 'Haiku 4.5';
  if (model.includes('sonnet'))      return 'Sonnet 4';
  if (model.includes('gpt-4o-mini')) return 'GPT-4o Mini';
  if (model.includes('gpt-4o'))      return 'GPT-4o';
  return model;
}

// Unique phases in order
const PHASES = [...new Set(PIPELINE_STEPS.map(s => s.phase))];

// ─── Formatters ──────────────────────────────────────────────────────────────

function fmt(n: number | null | undefined, decimals = 0) {
  if (n == null) return '—';
  return Number(n).toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function fmtCost(n: number | null | undefined) {
  if (n == null) return '—';
  const v = Number(n);
  if (v === 0) return '$0.00';
  if (v < 0.01) return `$${v.toFixed(4)}`;
  return `$${v.toFixed(2)}`;
}

function fmtDate(iso: string | null | undefined) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-US', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

// ─── Tooltip component (portal-based to escape overflow:hidden) ───────────────

const TOOLTIP_W = 300;

function Tooltip({ text }: { text: string }) {
  const [vis, setVis] = useState(false);
  const [coords, setCoords] = useState({ top: 0, left: 0 });
  const ref = useRef<HTMLSpanElement>(null);

  const show = () => {
    if (ref.current) {
      const r = ref.current.getBoundingClientRect();
      const rawLeft = r.left + r.width / 2;
      // Clamp so tooltip never overflows either viewport edge
      const left = Math.max(TOOLTIP_W / 2 + 8, Math.min(rawLeft, window.innerWidth - TOOLTIP_W / 2 - 8));
      setCoords({ top: r.top - 10, left });
    }
    setVis(true);
  };

  return (
    <span
      ref={ref}
      style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', marginLeft: 5, verticalAlign: 'middle' }}
      onMouseEnter={show}
      onMouseLeave={() => setVis(false)}
    >
      <span style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        width: 14, height: 14, borderRadius: '50%', fontSize: 9, fontWeight: 700,
        border: '1px solid var(--text-muted)', color: 'var(--text-muted)',
        cursor: 'help', lineHeight: 1, flexShrink: 0,
      }}>?</span>
      {vis && createPortal(
        <span style={{
          position: 'fixed',
          bottom: window.innerHeight - coords.top + 2,
          left: coords.left,
          transform: 'translateX(-50%)',
          background: '#1a1d2e', border: '1px solid var(--border)',
          borderRadius: 6, padding: '9px 13px',
          fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.55,
          width: TOOLTIP_W, zIndex: 9999, pointerEvents: 'none',
          boxShadow: '0 6px 24px rgba(0,0,0,0.5)',
          whiteSpace: 'normal',
        }}>
          {text}
          <span style={{
            position: 'absolute', top: '100%', left: '50%',
            transform: 'translateX(-50%)',
            borderWidth: '5px 5px 0', borderStyle: 'solid',
            borderColor: 'var(--border) transparent transparent',
          }} />
        </span>,
        document.body,
      )}
    </span>
  );
}

// ─── Tenant admin hover card ──────────────────────────────────────────────────

function AdminHover({ name, email, slug }: { name?: string | null; email?: string | null; slug?: string | null }) {
  const [vis, setVis] = useState(false);

  if (!name && !email) {
    return <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>—</span>;
  }

  return (
    <span
      style={{ position: 'relative', display: 'inline-block' }}
      onMouseEnter={() => setVis(true)}
      onMouseLeave={() => setVis(false)}
    >
      <span style={{ fontSize: 12, color: 'var(--text-muted)', cursor: 'default', borderBottom: '1px dashed rgba(255,255,255,0.2)' }}>
        {name || email}
      </span>
      {vis && (
        <span style={{
          position: 'absolute', top: 'calc(100% + 6px)', left: 0,
          background: '#1a1d2e', border: '1px solid var(--border)',
          borderRadius: 6, padding: '10px 14px',
          zIndex: 200, pointerEvents: 'none',
          boxShadow: '0 6px 24px rgba(0,0,0,0.5)',
          whiteSpace: 'nowrap', minWidth: 220,
        }}>
          <div style={{ fontSize: 9, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6, fontWeight: 700 }}>Tenant Admin</div>
          {name  && <div style={{ fontSize: 13, color: 'var(--text-primary)', fontWeight: 600 }}>{name}</div>}
          {email && <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 3 }}>{email}</div>}
          {slug  && <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 8 }}>slug / {slug}</div>}
        </span>
      )}
    </span>
  );
}

// ─── Stat card ───────────────────────────────────────────────────────────────

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div style={{
      flex: '1 1 0',
      background: 'var(--surface-2)',
      border: '1px solid var(--border)',
      borderRadius: 8,
      padding: '16px 20px',
    }}>
      <div style={{ fontSize: 10, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6, fontWeight: 700 }}>{label}</div>
      <div style={{ fontSize: 26, fontWeight: 700, color: 'var(--text-primary)', lineHeight: 1 }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6 }}>{sub}</div>}
    </div>
  );
}

// ─── Phase colour map ─────────────────────────────────────────────────────────

const PHASE_COLORS: Record<string, { bg: string; text: string }> = {
  'Ticket Create Pipeline': { bg: 'rgba(99,179,237,0.12)',  text: '#63b3ed' },
  'In-Ticket AI':           { bg: 'rgba(139,92,246,0.12)', text: '#a78bfa' },
  'Close Pipeline':         { bg: 'rgba(251,191,36,0.12)', text: '#fbbf24' },
  'KB Management':          { bg: 'rgba(52,211,153,0.12)', text: '#34d399' },
  'Phone':                  { bg: 'rgba(248,113,113,0.12)', text: '#f87171' },
};

// ─── Caller chip ─────────────────────────────────────────────────────────────

function CallerChip({ meta }: { meta: CallerMeta }) {
  const color = PHASE_COLORS[meta.phase] ?? { bg: 'rgba(255,255,255,0.08)', text: 'var(--text-muted)' };
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center' }}>
      <span style={{
        display: 'inline-block', padding: '2px 8px', borderRadius: 4,
        background: color.bg, color: color.text,
        fontSize: 11, fontWeight: 500,
      }}>
        {meta.label}
      </span>
      <Tooltip text={meta.tooltip} />
    </span>
  );
}

// ─── Table style constants ────────────────────────────────────────────────────

const TH: React.CSSProperties = {
  textAlign: 'left', padding: '10px 14px', fontSize: 10,
  color: 'var(--text-muted)', fontWeight: 700, textTransform: 'uppercase',
  letterSpacing: '0.08em', borderBottom: '1px solid var(--border)',
  whiteSpace: 'nowrap', background: 'var(--surface-2)',
};
const THR: React.CSSProperties = { ...TH, textAlign: 'right' };
const TD: React.CSSProperties = {
  padding: '12px 14px', fontSize: 13, color: 'var(--text-secondary)',
  borderBottom: '1px solid rgba(255,255,255,0.04)', verticalAlign: 'middle',
};
const TDR: React.CSSProperties = { ...TD, textAlign: 'right' };
const SUB_TH: React.CSSProperties = {
  ...TH, fontSize: 9, padding: '6px 10px', background: 'var(--surface-1)',
  borderBottom: '1px solid rgba(255,255,255,0.06)',
};
const SUB_THR: React.CSSProperties = { ...SUB_TH, textAlign: 'right' };
const SUB_TD: React.CSSProperties  = { ...TD, padding: '8px 10px', fontSize: 12 };
const SUB_TDR: React.CSSProperties = { ...SUB_TD, textAlign: 'right' };

// ─── Main panel ───────────────────────────────────────────────────────────────

export function UsagePanel() {
  const [period,      setPeriod]      = useState<Period>('month');
  const [data,        setData]        = useState<any>(null);
  const [loading,     setLoading]     = useState(true);
  const [expanded,    setExpanded]    = useState<string | number | null>(null);
  const [tenantFilter, setTenantFilter] = useState<number | null>(null);
  const [useCustom,   setUseCustom]  = useState(false);
  const [startDate,   setStartDate]  = useState('');
  const [endDate,     setEndDate]    = useState('');
  const isSuperAdmin = useAuthStore((s) => s.isSuperAdmin);

  useEffect(() => {
    setLoading(true);
    const params: any = {};
    if (useCustom && startDate && endDate) {
      params.start_date = startDate;
      params.end_date = endDate;
    } else {
      params.period = period;
    }
    if (tenantFilter) params.tenant_id = tenantFilter;
    api.getUsageStats(params)
      .then(setData)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [period, tenantFilter, useCustom, startDate, endDate]);

  const grand: any      = data?.grand_total || {};
  const byTenant: any[] = data?.by_tenant   || [];
  const byCaller: any[] = data?.by_caller   || [];
  const tenants: any[]  = data?.tenants     || [];

  // Build per-tenant caller map: (tenant_id | 'null') → rows
  const callersByTenant: Record<string | number, any[]> = {};
  for (const row of byCaller) {
    const key = row.tenant_id ?? 'null';
    if (!callersByTenant[key]) callersByTenant[key] = [];
    callersByTenant[key].push(row);
  }

  const periodLabel = useCustom && startDate && endDate
    ? `${startDate} — ${endDate}`
    : (PERIODS.find(p => p.key === period)?.label ?? period);

  // ── CSV export ──────────────────────────────────────────────────────────────
  function exportCsv() {
    const rows: string[][] = [];
    rows.push(['Tenant', 'Pipeline Step', 'Phase', 'Model', 'Calls', 'Input Tokens', 'Output Tokens', 'Cost (USD)', 'Avg Tokens/Call']);
    for (const t of byTenant) {
      const tid = t.tenant_id ?? 'null';
      const callerRows = callersByTenant[tid] || [];
      for (const cr of callerRows) {
        const meta = STEP_BY_KEY[cr.caller];
        const calls = Number(cr.calls || 0);
        rows.push([
          t.tenant_name,
          meta?.label ?? cr.caller,
          meta?.phase ?? 'Other',
          shortModel(cr.model),
          String(calls),
          String(cr.input_tokens || 0),
          String(cr.output_tokens || 0),
          Number(cr.cost_usd || 0).toFixed(4),
          calls > 0 ? String(Math.round(Number(cr.input_tokens || 0) / calls)) : '0',
        ]);
      }
    }
    const csv = rows.map(r => r.map(c => `"${c.replace(/"/g, '""')}"`).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `token-usage-${periodLabel.replace(/\s/g, '_')}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div style={{ width: '100%' }}>

      {/* ── Header ── */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <h3 style={{ margin: 0, fontSize: 16, color: 'var(--text-primary)' }}>LLM Token Usage</h3>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          {PERIODS.map(p => (
            <button
              key={p.key}
              className={`btn btn-sm ${!useCustom && period === p.key ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => { setUseCustom(false); setPeriod(p.key); }}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* ── Filters row ── */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 20, flexWrap: 'wrap' }}>
        {/* Tenant filter */}
        {isSuperAdmin() && tenants.length > 0 && (
          <select
            value={tenantFilter ?? ''}
            onChange={(e) => setTenantFilter(e.target.value ? Number(e.target.value) : null)}
            style={{
              background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 6,
              padding: '5px 10px', fontSize: 12, color: 'var(--text-secondary)',
            }}
          >
            <option value="">All tenants</option>
            {tenants.map((t: any) => (
              <option key={t.id} value={t.id}>{t.name}</option>
            ))}
          </select>
        )}

        {/* Date range */}
        <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
          <input
            type="date"
            value={startDate}
            onChange={(e) => { setStartDate(e.target.value); if (e.target.value && endDate) setUseCustom(true); }}
            style={{
              background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 6,
              padding: '5px 8px', fontSize: 11, color: 'var(--text-secondary)', width: 130,
            }}
          />
          <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>to</span>
          <input
            type="date"
            value={endDate}
            onChange={(e) => { setEndDate(e.target.value); if (startDate && e.target.value) setUseCustom(true); }}
            style={{
              background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 6,
              padding: '5px 8px', fontSize: 11, color: 'var(--text-secondary)', width: 130,
            }}
          />
          {useCustom && (
            <button
              className="btn btn-sm btn-ghost"
              onClick={() => { setUseCustom(false); setStartDate(''); setEndDate(''); }}
              style={{ fontSize: 10, padding: '4px 8px' }}
            >
              Clear
            </button>
          )}
        </div>

        {/* Spacer */}
        <div style={{ flex: 1 }} />

        {/* CSV export */}
        <button
          className="btn btn-sm btn-ghost"
          onClick={exportCsv}
          disabled={loading || byCaller.length === 0}
          style={{ fontSize: 11 }}
        >
          Export CSV
        </button>
      </div>

      {/* ── Billing links ── */}
      {isSuperAdmin() && (
        <div style={{
          display: 'flex', gap: 16, marginBottom: 16, fontSize: 11, color: 'var(--text-muted)',
          alignItems: 'center',
        }}>
          <span style={{ fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', fontSize: 9 }}>Check balances:</span>
          <a href="https://console.anthropic.com/settings/billing" target="_blank" rel="noreferrer"
            style={{ color: 'var(--accent-green)', textDecoration: 'none', borderBottom: '1px dashed rgba(52,211,153,0.3)' }}>
            Anthropic
          </a>
          <a href="https://platform.openai.com/settings/organization/billing/overview" target="_blank" rel="noreferrer"
            style={{ color: 'var(--accent-green)', textDecoration: 'none', borderBottom: '1px dashed rgba(52,211,153,0.3)' }}>
            OpenAI
          </a>
          <a href="https://dash.voyageai.com/billing" target="_blank" rel="noreferrer"
            style={{ color: 'var(--accent-green)', textDecoration: 'none', borderBottom: '1px dashed rgba(52,211,153,0.3)' }}>
            Voyage AI
          </a>
        </div>
      )}

      {/* ── Grand total cards ── */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 28 }}>
        <StatCard
          label="Total API Calls"
          value={loading ? '…' : fmt(grand.total_calls)}
          sub={`${periodLabel.toLowerCase()} total`}
        />
        <StatCard
          label="Input Tokens"
          value={loading ? '…' : fmt(grand.total_input_tokens)}
          sub="prompt tokens"
        />
        <StatCard
          label="Output Tokens"
          value={loading ? '…' : fmt(grand.total_output_tokens)}
          sub="completion tokens"
        />
        <StatCard
          label="Estimated Cost"
          value={loading ? '…' : fmtCost(grand.total_cost_usd)}
          sub="USD (estimated)"
        />
      </div>

      {/* ── Main table ── */}
      {loading ? (
        <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)', fontSize: 13 }}>
          Loading…
        </div>
      ) : byTenant.length === 0 ? (
        <div style={{
          padding: 40, textAlign: 'center', color: 'var(--text-muted)', fontSize: 13,
          background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 8,
        }}>
          No usage recorded for this period.
        </div>
      ) : (
        <div style={{
          background: 'var(--surface-2)', border: '1px solid var(--border)',
          borderRadius: 8, overflow: 'hidden', width: '100%',
        }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', tableLayout: 'fixed' }}>
            <colgroup>
              <col style={{ width: isSuperAdmin() ? '20%' : '24%' }} />
              {isSuperAdmin() && <col style={{ width: '17%' }} />}
              <col style={{ width: isSuperAdmin() ? '8%' : '10%' }} />
              <col style={{ width: isSuperAdmin() ? '13%' : '16%' }} />
              <col style={{ width: isSuperAdmin() ? '13%' : '16%' }} />
              <col style={{ width: isSuperAdmin() ? '10%' : '12%' }} />
              <col style={{ width: isSuperAdmin() ? '16%' : '19%' }} />
              <col style={{ width: '3%' }} />
            </colgroup>
            <thead>
              <tr>
                <th style={TH}>Tenant</th>
                {isSuperAdmin() && <th style={TH}>Admin Contact</th>}
                <th style={THR}>Calls</th>
                <th style={THR}>Input Tokens</th>
                <th style={THR}>Output Tokens</th>
                <th style={THR}>Est. Cost</th>
                <th style={THR}>Last Call</th>
                <th style={TH}></th>
              </tr>
            </thead>
            <tbody>
              {byTenant.map((row: any) => {
                const tid  = row.tenant_id ?? 'null';
                const open = expanded === tid;

                const callerRows = callersByTenant[tid] || [];

                // Group by caller+model to show per-model cost breakdown
                type CallerStats = { calls: number; input_tokens: number; output_tokens: number; cost_usd: number; model: string };
                // callerModelMap: "caller|model" → stats
                const callerModelMap: Record<string, CallerStats> = {};
                // callerKeys: set of unique caller keys that have data
                const callerKeysWithData = new Set<string>();
                for (const cr of callerRows) {
                  const caller = cr.caller || 'unknown';
                  const model  = cr.model  || 'unknown';
                  const cmKey  = `${caller}|${model}`;
                  callerKeysWithData.add(caller);
                  if (!callerModelMap[cmKey]) callerModelMap[cmKey] = { calls: 0, input_tokens: 0, output_tokens: 0, cost_usd: 0, model };
                  callerModelMap[cmKey].calls         += Number(cr.calls         || 0);
                  callerModelMap[cmKey].input_tokens  += Number(cr.input_tokens  || 0);
                  callerModelMap[cmKey].output_tokens += Number(cr.output_tokens || 0);
                  callerModelMap[cmKey].cost_usd      += Number(cr.cost_usd      || 0);
                }

                // Build ordered list: all known pipeline steps (even if 0) +
                // any unknown callers appended at the end.
                const seen = new Set<string>();
                const orderedSteps: Array<{ meta: CallerMeta; stats: CallerStats }> = [];
                for (const meta of PIPELINE_STEPS) {
                  if (seen.has(meta.key)) continue;
                  seen.add(meta.key);
                  // Find all model entries for this caller
                  const modelEntries = Object.entries(callerModelMap).filter(([k]) => k.startsWith(meta.key + '|'));
                  if (modelEntries.length === 0) {
                    // No data — show single zero row
                    orderedSteps.push({ meta, stats: { calls: 0, input_tokens: 0, output_tokens: 0, cost_usd: 0, model: '' } });
                  } else {
                    for (const [, stats] of modelEntries) {
                      orderedSteps.push({ meta, stats });
                    }
                  }
                }
                // Append any DB callers not in our known list
                for (const caller of callerKeysWithData) {
                  if (seen.has(caller)) continue;
                  seen.add(caller);
                  const modelEntries = Object.entries(callerModelMap).filter(([k]) => k.startsWith(caller + '|'));
                  for (const [, stats] of modelEntries) {
                    orderedSteps.push({
                      meta: { key: caller, label: caller, phase: 'Other', tooltip: `Raw caller key: ${caller}` },
                      stats,
                    });
                  }
                }

                const ZERO_STYLE: React.CSSProperties = { opacity: 0.35 };

                return [
                  <tr
                    key={`row-${tid}`}
                    style={{ cursor: 'pointer' }}
                    onClick={() => setExpanded(open ? null : tid)}
                  >
                    <td style={{ ...TD, fontWeight: 600, color: 'var(--text-primary)' }}>
                      {row.tenant_name}
                    </td>
                    {isSuperAdmin() && (
                      <td style={TD}>
                        <AdminHover
                          name={row.admin_name}
                          email={row.admin_email}
                          slug={row.tenant_slug}
                        />
                      </td>
                    )}
                    <td style={TDR}>{fmt(row.total_calls)}</td>
                    <td style={TDR}>{fmt(row.total_input_tokens)}</td>
                    <td style={TDR}>{fmt(row.total_output_tokens)}</td>
                    <td style={{ ...TDR, color: 'var(--accent-green)', fontWeight: 600 }}>
                      {fmtCost(row.total_cost_usd)}
                    </td>
                    <td style={{ ...TDR, fontSize: 11, color: 'var(--text-muted)' }}>
                      {fmtDate(row.last_call)}
                    </td>
                    <td style={{ ...TD, textAlign: 'center', paddingLeft: 0, paddingRight: 8 }}>
                      <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>{open ? '▲' : '▼'}</span>
                    </td>
                  </tr>,

                  open && (
                    <tr key={`detail-${tid}`}>
                      <td colSpan={isSuperAdmin() ? 8 : 7} style={{ padding: 0, borderBottom: '1px solid var(--border)' }}>
                        <div style={{ background: 'var(--surface-1)', padding: '4px 0 16px' }}>
                          <div style={{
                            fontSize: 9, color: 'var(--text-muted)', textTransform: 'uppercase',
                            letterSpacing: '0.08em', fontWeight: 700, padding: '10px 14px 8px',
                          }}>
                            Breakdown by feature
                          </div>
                          {/* Inner table mirrors outer colgroup exactly so numeric columns line up */}
                          <table style={{ width: '100%', borderCollapse: 'collapse', tableLayout: 'fixed' }}>
                              <colgroup>
                                <col style={{ width: isSuperAdmin() ? '20%' : '24%' }} />
                                {isSuperAdmin() && <col style={{ width: '17%' }} />}
                                <col style={{ width: isSuperAdmin() ? '8%' : '10%' }} />
                                <col style={{ width: isSuperAdmin() ? '13%' : '16%' }} />
                                <col style={{ width: isSuperAdmin() ? '13%' : '16%' }} />
                                <col style={{ width: isSuperAdmin() ? '10%' : '12%' }} />
                                <col style={{ width: isSuperAdmin() ? '16%' : '19%' }} />
                                <col style={{ width: '3%' }} />
                              </colgroup>
                              <thead>
                                <tr>
                                  <th style={SUB_TH}>Pipeline Step</th>
                                  {isSuperAdmin() && <th style={SUB_TH}>Model</th>}
                                  <th style={SUB_THR}>Calls</th>
                                  <th style={SUB_THR}>Input Tokens</th>
                                  <th style={SUB_THR}>Output Tokens</th>
                                  <th style={SUB_THR}>Cost</th>
                                  <th style={SUB_THR}>Avg Tokens / Call</th>
                                  <th style={SUB_TH}></th>
                                </tr>
                              </thead>
                              <tbody>
                                {PHASES.concat(orderedSteps.some(r => r.meta.phase === 'Other') ? ['Other'] : []).map(phase => {
                                  const rows = orderedSteps.filter(r => r.meta.phase === phase);
                                  if (rows.length === 0) return null;
                                  const colSpanTotal = isSuperAdmin() ? 8 : 7;
                                  return [
                                    // Phase header row
                                    <tr key={`phase-${phase}`}>
                                      <td colSpan={colSpanTotal} style={{
                                        padding: '10px 14px 4px',
                                        fontSize: 9, fontWeight: 700, letterSpacing: '0.1em',
                                        textTransform: 'uppercase',
                                        color: PHASE_COLORS[phase]?.text ?? 'var(--text-muted)',
                                        borderTop: '1px solid rgba(255,255,255,0.06)',
                                        background: 'var(--surface-1)',
                                      }}>
                                        {phase}
                                      </td>
                                    </tr>,
                                    // Step rows
                                    ...rows.map(({ meta, stats: s }, i) => (
                                      <tr key={`${meta.key}-${s.model || i}`} style={s.calls === 0 ? ZERO_STYLE : {}}>
                                        <td style={SUB_TD}>
                                          <CallerChip meta={meta} />
                                          {!isSuperAdmin() && s.model && (
                                            <span style={{ marginLeft: 6, fontSize: 10, color: 'var(--text-muted)' }}>
                                              {shortModel(s.model)}
                                            </span>
                                          )}
                                        </td>
                                        {isSuperAdmin() && (
                                          <td style={{ ...SUB_TD, fontSize: 11, color: 'var(--text-muted)' }}>
                                            {s.model ? shortModel(s.model) : '—'}
                                          </td>
                                        )}
                                        <td style={SUB_TDR}>{s.calls > 0 ? fmt(s.calls) : <span style={{ color: 'var(--text-muted)' }}>—</span>}</td>
                                        <td style={SUB_TDR}>{s.calls > 0 ? fmt(s.input_tokens) : <span style={{ color: 'var(--text-muted)' }}>—</span>}</td>
                                        <td style={SUB_TDR}>{s.calls > 0 ? fmt(s.output_tokens) : <span style={{ color: 'var(--text-muted)' }}>—</span>}</td>
                                        <td style={{ ...SUB_TDR, color: s.calls > 0 ? 'var(--accent-green)' : 'var(--text-muted)' }}>
                                          {s.calls > 0 ? fmtCost(s.cost_usd) : '—'}
                                        </td>
                                        <td style={{ ...SUB_TDR, color: 'var(--text-muted)' }}>
                                          {s.calls > 0 ? fmt(Math.round(s.input_tokens / s.calls)) : '—'}
                                        </td>
                                        <td style={SUB_TD}></td>
                                      </tr>
                                    )),
                                  ];
                                })}
                              </tbody>
                            </table>
                        </div>
                      </td>
                    </tr>
                  ),
                ].filter(Boolean);
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
