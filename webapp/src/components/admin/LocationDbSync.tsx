import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { LocationDbSyncConfig, LevelConfig } from '../../types';

const DB_TYPES = [
  { value: 'postgresql', label: 'PostgreSQL', defaultPort: 5432 },
  { value: 'mysql',      label: 'MySQL',      defaultPort: 3306 },
  { value: 'mssql',      label: 'SQL Server', defaultPort: 1433 },
];

const LEVEL_KEYS = ['company', 'country', 'state', 'city', 'store'] as const;
type LevelKey = typeof LEVEL_KEYS[number];

const LEVEL_LABELS: Record<LevelKey, string> = {
  company: 'Company',
  country: 'Country',
  state:   'State',
  city:    'City',
  store:   'Store',
};

const emptyLevels = (): Record<LevelKey, LevelConfig> => ({
  company: { column: '', fixed: '' },
  country: { column: '', fixed: '' },
  state:   { column: '', fixed: '' },
  city:    { column: '', fixed: '' },
  store:   { column: '', fixed: '' },
});

export function LocationDbSync() {
  const [saved, setSaved] = useState<LocationDbSyncConfig | null>(null);

  // Connection
  const [dbType, setDbType]     = useState('postgresql');
  const [host, setHost]         = useState('');
  const [port, setPort]         = useState(5432);
  const [dbname, setDbname]     = useState('');
  const [dbUser, setDbUser]     = useState('');
  const [password, setPassword] = useState('');

  // Source table
  const [schema, setSchema] = useState('');
  const [table,  setTable]  = useState('');

  // Level mapping
  const [levels,  setLevels]  = useState<Record<LevelKey, LevelConfig>>(emptyLevels());
  const [columns, setColumns] = useState<string[]>([]);
  const [previewRows, setPreviewRows] = useState<Record<string, string | null>[]>([]);

  const [testStatus, setTestStatus] = useState<'idle' | 'testing' | 'ok' | 'error'>('idle');
  const [testError,  setTestError]  = useState('');
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [syncStatus, setSyncStatus] = useState<'idle' | 'running' | 'done' | 'error'>('idle');
  const [syncResult, setSyncResult] = useState<{ created: number; skipped: number; linked: number; total_fetched: number } | null>(null);
  const [syncError,  setSyncError]  = useState('');
  const [copied, setCopied] = useState<'url' | 'token' | null>(null);

  useEffect(() => {
    api.getLocationDbSync().then((cfg) => {
      if (!cfg) return;
      setSaved(cfg);
      setDbType(cfg.db_type || 'postgresql');
      setHost(cfg.host || '');
      setPort(cfg.port || 5432);
      setDbname(cfg.dbname || '');
      setDbUser(cfg.db_user || '');
      setSchema(cfg.schema || '');
      setTable(cfg.table || '');
      if (cfg.levels) setLevels({ ...emptyLevels(), ...cfg.levels });
      if (cfg.preview_columns?.length) setColumns(cfg.preview_columns);
    }).catch(() => {});
  }, []);

  const handleDbTypeChange = (t: string) => {
    setDbType(t);
    const def = DB_TYPES.find((d) => d.value === t);
    if (def) setPort(def.defaultPort);
  };

  const handleTableChange = (v: string) => {
    setTable(v);
    setColumns([]);
    setPreviewRows([]);
    setTestStatus('idle');
  };

  const setLevel = (key: LevelKey, field: keyof LevelConfig, value: string) => {
    setLevels((prev) => ({ ...prev, [key]: { ...prev[key], [field]: value } }));
  };

  const hasConfig      = !!saved?.webhook_token;
  const credsFilled    = host && dbname && dbUser;
  const passwordFilled = !!password;
  const canTest        = table && credsFilled && (passwordFilled || hasConfig);
  const hasLevelConfig = LEVEL_KEYS.some((k) => levels[k].column || levels[k].fixed.trim());
  const canSave        = table && hasLevelConfig && credsFilled && (passwordFilled || hasConfig);

  const handleTest = async () => {
    setTestStatus('testing');
    setTestError('');
    setColumns([]);
    setPreviewRows([]);
    try {
      const result = await api.testLocationDbSync({
        db_type: dbType, host, port, dbname, db_user: dbUser,
        password: password || undefined,
        schema: schema || undefined,
        table,
      });
      setColumns(result.columns);
      setPreviewRows(result.rows);
      setTestStatus('ok');
    } catch (e: any) {
      setTestError(e.message);
      setTestStatus('error');
    }
  };

  const handleSave = async () => {
    setSaveStatus('saving');
    try {
      const result = await api.saveLocationDbSync({
        db_type: dbType, host, port, dbname, db_user: dbUser,
        password: password || undefined,
        schema: schema || undefined,
        table,
        levels,
        preview_columns: columns,
      });
      setPassword('');
      setSaved((prev) => ({
        ...(prev ?? {} as LocationDbSyncConfig),
        db_type: dbType, host, port, dbname, db_user: dbUser,
        schema, table, levels,
        preview_columns: columns,
        webhook_token: result.webhook_token,
      }));
      setSaveStatus('saved');
      setTimeout(() => setSaveStatus('idle'), 2500);
    } catch {
      setSaveStatus('error');
      setTimeout(() => setSaveStatus('idle'), 3000);
    }
  };

  const handleSync = async () => {
    const token = saved?.webhook_token;
    if (!token) return;
    setSyncStatus('running');
    setSyncResult(null);
    setSyncError('');
    try {
      const result = await api.runLocationDbSync(token);
      setSyncResult(result);
      setSyncStatus('done');
      setSaved((prev) => prev ? { ...prev, last_sync_at: new Date().toISOString(), last_error: null, last_result: result } : prev);
    } catch (e: any) {
      setSyncError(e.message);
      setSyncStatus('error');
    }
  };

  const copyText = (text: string, type: 'url' | 'token') => {
    navigator.clipboard.writeText(text).catch(() => {});
    setCopied(type);
    setTimeout(() => setCopied(null), 2000);
  };

  const webhookUrl = `${window.location.origin}/api/hierarchies/locations/db-sync/run`;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20, padding: '4px 0 20px' }}>

      <div>
        <h4 style={{ margin: '0 0 4px', color: 'var(--text-primary)', fontSize: 14 }}>Database Sync</h4>
        <p style={{ margin: 0, color: 'var(--text-muted)', fontSize: 13, lineHeight: 1.5 }}>
          Connect an external database to keep your location hierarchy in sync. Configure the connection below, then trigger syncs manually or via any scheduler using the webhook endpoint.
        </p>
      </div>

      {/* ── Connection ─────────────────────────────────── */}
      <Section title="Connection">
        <div style={{ display: 'grid', gridTemplateColumns: '160px 1fr 90px', gap: 10 }}>
          <Field label="Type">
            <select className="form-select" value={dbType} onChange={(e) => handleDbTypeChange(e.target.value)} style={{ fontSize: 13 }}>
              {DB_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
            </select>
          </Field>
          <Field label="Host">
            <input className="form-input" value={host} onChange={(e) => setHost(e.target.value)} placeholder="db.example.com" style={mono} />
          </Field>
          <Field label="Port">
            <input className="form-input" type="number" value={port} onChange={(e) => setPort(Number(e.target.value))} style={mono} />
          </Field>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
          <Field label="Database">
            <input className="form-input" value={dbname} onChange={(e) => setDbname(e.target.value)} placeholder="my_database" style={mono} />
          </Field>
          <Field label="Username">
            <input className="form-input" value={dbUser} onChange={(e) => setDbUser(e.target.value)} placeholder="db_user" style={mono} autoComplete="off" />
          </Field>
          <Field label="Password">
            <input className="form-input" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
              placeholder={hasConfig ? '••••••• (saved)' : 'password'} autoComplete="new-password" />
          </Field>
        </div>
      </Section>

      {/* ── Source Table ────────────────────────────────── */}
      <Section title="Source Table">
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: 10 }}>
          <Field label="Schema (optional)">
            <input className="form-input" value={schema} onChange={(e) => setSchema(e.target.value)} placeholder="public" style={mono} />
          </Field>
          <Field label="Table">
            <input className="form-input" value={table} onChange={(e) => handleTableChange(e.target.value)} placeholder="stores" style={mono} />
          </Field>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <button className="btn btn-sm btn-ghost" onClick={handleTest} disabled={!canTest || testStatus === 'testing'}>
            {testStatus === 'testing' ? 'Fetching…' : 'Fetch Columns'}
          </button>
          {testStatus === 'ok' && (
            <span style={{ fontSize: 13, color: 'var(--color-success)' }}>
              {columns.length} columns · {previewRows.length} sample rows
            </span>
          )}
          {testStatus === 'error' && (
            <span style={{ fontSize: 13, color: 'var(--color-danger)' }}>{testError}</span>
          )}
        </div>
      </Section>

      {/* ── Preview ─────────────────────────────────────── */}
      {testStatus === 'ok' && previewRows.length > 0 && (
        <div style={{ overflowX: 'auto', border: '1px solid var(--border-color)', borderRadius: 6 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr>
                {columns.map((c) => (
                  <th key={c} style={{ padding: '6px 10px', textAlign: 'left', color: 'var(--text-muted)', background: 'var(--bg-secondary)', borderBottom: '1px solid var(--border-color)', whiteSpace: 'nowrap' }}>
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {previewRows.map((row, i) => (
                <tr key={i}>
                  {columns.map((c) => (
                    <td key={c} style={{ padding: '5px 10px', borderBottom: '1px solid var(--border-subtle)', color: 'var(--text-secondary)', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {row[c] ?? <span style={{ color: 'var(--text-muted)', fontStyle: 'italic' }}>null</span>}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── Hierarchy Levels ────────────────────────────── */}
      {(columns.length > 0 || hasConfig) && (
        <Section title="Hierarchy Levels">
          <p style={{ margin: '0 0 4px', fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.5 }}>
            Map each level to a column from your table, or type a fixed value (useful for a single company name).
            Leave both blank to skip that level entirely.
          </p>

          {/* Header row */}
          <div style={{ display: 'grid', gridTemplateColumns: '72px 1fr 1fr', gap: 8, paddingBottom: 4, borderBottom: '1px solid var(--border-subtle)' }}>
            <span style={headerStyle}>Level</span>
            <span style={headerStyle}>Column from table</span>
            <span style={headerStyle}>Fixed value</span>
          </div>

          {LEVEL_KEYS.map((key) => {
            const lv = levels[key];
            const colSelected = !!lv.column;
            return (
              <div key={key} style={{ display: 'grid', gridTemplateColumns: '72px 1fr 1fr', gap: 8, alignItems: 'center' }}>
                <span style={{ fontSize: 13, color: 'var(--text-secondary)', fontWeight: 500 }}>
                  {LEVEL_LABELS[key]}
                </span>
                <select
                  className="form-select"
                  value={lv.column}
                  onChange={(e) => setLevel(key, 'column', e.target.value)}
                  style={{ fontSize: 13 }}
                >
                  <option value="">— none —</option>
                  {columns.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
                <input
                  className="form-input"
                  value={lv.fixed}
                  onChange={(e) => setLevel(key, 'fixed', e.target.value)}
                  placeholder={colSelected ? '(using column)' : 'e.g. Acme Corp'}
                  disabled={colSelected}
                  style={{ fontSize: 13, opacity: colSelected ? 0.4 : 1 }}
                />
              </div>
            );
          })}
        </Section>
      )}

      {/* ── Save ────────────────────────────────────────── */}
      {canSave && (
        <button
          className={`btn btn-sm ${saveStatus === 'saved' ? 'btn-ghost' : 'btn-primary'}`}
          onClick={handleSave}
          disabled={saveStatus === 'saving'}
          style={{ alignSelf: 'flex-start' }}
        >
          {saveStatus === 'saving' ? 'Saving…' : saveStatus === 'saved' ? 'Saved' : saveStatus === 'error' ? 'Save failed — retry' : 'Save Configuration'}
        </button>
      )}

      {/* ── Webhook Integration ──────────────────────────── */}
      {hasConfig && (
        <Section title={<>Webhook Endpoint <span style={{ fontWeight: 400, fontSize: 12, color: 'var(--text-muted)' }}>HTTP POST · use to trigger sync from any scheduler</span></>}>
          <Field label="Endpoint URL">
            <CopyRow text={webhookUrl} copied={copied === 'url'} onCopy={() => copyText(webhookUrl, 'url')} />
          </Field>
          <Field label="Authorization Header">
            <CopyRow text={`Bearer ${saved!.webhook_token}`} copied={copied === 'token'} onCopy={() => copyText(`Bearer ${saved!.webhook_token}`, 'token')} />
          </Field>
        </Section>
      )}

      {/* ── Manual sync ─────────────────────────────────── */}
      {hasConfig && (
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 14 }}>
          <button className="btn btn-sm btn-ghost" onClick={handleSync} disabled={syncStatus === 'running'}>
            {syncStatus === 'running' ? 'Syncing…' : 'Sync Now'}
          </button>
          {saved?.last_sync_at && syncStatus === 'idle' && (
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
              Last sync: {new Date(saved.last_sync_at).toLocaleString()}
              {saved.last_result && (
                <span style={{ marginLeft: 8, color: 'var(--text-secondary)' }}>
                  ({saved.last_result.created} created · {saved.last_result.skipped} already existed)
                </span>
              )}
            </span>
          )}
          {syncStatus === 'done' && syncResult && (
            <span style={{ fontSize: 12, color: 'var(--color-success)' }}>
              Done — {syncResult.total_fetched} rows · {syncResult.created} created · {syncResult.skipped} already existed
            </span>
          )}
          {syncStatus === 'error' && <span style={{ fontSize: 12, color: 'var(--color-danger)' }}>{syncError}</span>}
          {saved?.last_error && syncStatus === 'idle' && (
            <span style={{ fontSize: 12, color: 'var(--color-warning)' }}>Last error: {saved.last_error}</span>
          )}
        </div>
      )}
    </div>
  );
}

// ── Helpers ───────────────────────────────────────────────

function Section({ title, children }: { title: React.ReactNode; children: React.ReactNode }) {
  return (
    <div style={{ background: 'var(--bg-secondary)', borderRadius: 8, padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{title}</span>
      {children}
    </div>
  );
}

function Field({ label, children }: { label: React.ReactNode; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
      <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
        {label}
      </label>
      {children}
    </div>
  );
}

function CopyRow({ text, copied, onCopy }: { text: string; copied: boolean; onCopy: () => void }) {
  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
      <code style={{ flex: 1, padding: '7px 10px', background: 'var(--bg-tertiary)', borderRadius: 5, fontSize: 12, color: 'var(--text-primary)', wordBreak: 'break-all' }}>
        {text}
      </code>
      <button className="btn btn-xs btn-ghost" onClick={onCopy} style={{ whiteSpace: 'nowrap' }}>
        {copied ? 'Copied!' : 'Copy'}
      </button>
    </div>
  );
}

const mono: React.CSSProperties        = { fontFamily: 'monospace', fontSize: 13 };
const headerStyle: React.CSSProperties = { fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' };
