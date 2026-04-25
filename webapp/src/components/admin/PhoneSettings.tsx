import { useEffect, useRef, useState } from 'react';
import { api } from '../../api/client';
import type { PhoneAgent, PhoneConfig, PhoneSession, MessagingConfig, MessagingConversation, Message, MessagingTemplate, MessagingStats } from '../../types';

// ── Constants ────────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
  active:          'var(--t-info)',
  resolved:        'var(--t-accent)',
  transferred:     'var(--t-accent)',
  email_collected: 'var(--t-warning)',
  abandoned:       'var(--t-text-muted)',
};

const STATUS_LABELS: Record<string, string> = {
  active:          'Active',
  resolved:        'Resolved',
  transferred:     'Transferred',
  email_collected: 'Email Collected',
  abandoned:       'Abandoned',
};

const LLM_MODEL_OPTIONS = [
  { value: 'claude-haiku-4-5@20251001',   label: 'Claude Haiku 4.5 (Fast, Low Cost)' },
  { value: 'claude-sonnet-4-20250514',    label: 'Claude Sonnet 4 (Balanced)' },
  { value: 'claude-3-5-haiku-20241022',   label: 'Claude 3.5 Haiku (Legacy)' },
  { value: '__custom__',                  label: 'Custom...' },
];

const AUDIO_FORMAT_OPTIONS = [
  { value: 'ulaw_8000',  label: 'ulaw_8000 — Telephony (Standard)' },
  { value: 'pcm_16000',  label: 'pcm_16000 — HD Voice' },
  { value: 'pcm_44100',  label: 'pcm_44100 — Studio Quality' },
];

const LANG_FLAGS: Record<string, string> = { en: 'EN', es: 'ES', fr: 'FR', de: 'DE', pt: 'PT' };
const LANG_LABELS: Record<string, string> = { en: 'English', es: 'Spanish', fr: 'French', de: 'German', pt: 'Portuguese' };

const ALL_TOOLS = [
  { key: 'search_kb',        label: 'KB Search' },
  { key: 'create_ticket',    label: 'Ticket Creation' },
  { key: 'identify_caller',  label: 'Caller Identification' },
  { key: 'attempt_transfer', label: 'Human Transfer' },
  { key: 'collect_email',    label: 'Email Collection' },
];

const WEBHOOK_LABELS: Record<string, string> = {
  tool_search_kb:       'Search Knowledge Base',
  tool_create_ticket:   'Create Ticket',
  tool_identify_caller: 'Identify Caller',
  tool_attempt_transfer:'Attempt Transfer',
  tool_collect_email:   'Collect Email',
  webhook_call_ended:   'Call Ended',
  ivr_greeting:         'IVR Greeting',
};

// ── Helpers ──────────────────────────────────────────────

function formatDuration(seconds?: number): string {
  if (!seconds) return '—';
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function resolveModelSelectValue(llm_model: string | null | undefined): string {
  if (!llm_model) return LLM_MODEL_OPTIONS[0].value;
  if (LLM_MODEL_OPTIONS.some((o) => o.value === llm_model && o.value !== '__custom__')) return llm_model;
  return '__custom__';
}

// ── Sub-components ───────────────────────────────────────

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  function handleCopy() {
    navigator.clipboard.writeText(value).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    }).catch(() => {});
  }
  return (
    <button type="button" className="btn btn-ghost" onClick={handleCopy}
      style={{ fontSize: 12, padding: '4px 10px', flexShrink: 0 }}>
      {copied ? 'Copied!' : 'Copy'}
    </button>
  );
}

function WebhookRow({ label, url }: { label: string; url: string }) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <label className="form-label" style={{ marginBottom: 2 }}>{label}</label>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <input ref={ref} readOnly value={url} onFocus={() => ref.current?.select()}
          className="form-input"
          style={{ fontFamily: 'monospace', fontSize: 12, background: 'var(--t-panel-alt)', color: 'var(--t-text-muted)', flex: 1 }} />
        <CopyButton value={url} />
      </div>
    </div>
  );
}

function DeployStepper({ agent }: { agent: PhoneAgent }) {
  const steps = [
    { label: 'Configure', desc: 'Persona & voice set', done: Boolean(agent.name && agent.voice_id) },
    { label: 'Deploy', desc: 'ElevenLabs agent live', done: agent.is_deployed },
    { label: 'Activate', desc: 'Number linked & active', done: agent.is_active && agent.is_number_linked },
  ];
  return (
    <div style={{
      display: 'flex', gap: 0, padding: '16px 24px', width: '100%', boxSizing: 'border-box',
      background: 'var(--t-panel)', borderRadius: 8,
      border: '1px solid var(--t-border)',
    }}>
      {steps.map((s, i) => (
        <div key={s.label} style={{ display: 'flex', alignItems: 'center', flex: 1 }}>
          <div style={{
            width: 36, height: 36, borderRadius: '50%', display: 'flex',
            alignItems: 'center', justifyContent: 'center', fontSize: 15, fontWeight: 700,
            background: s.done ? 'var(--t-accent)' : 'var(--t-panel-alt)',
            color: s.done ? 'var(--t-panel)' : 'var(--t-text-bright)',
            border: s.done ? '2px solid var(--t-accent)' : '2px solid var(--t-border)',
            boxShadow: s.done ? '0 0 8px var(--t-accent-border)' : 'none',
            flexShrink: 0,
          }}>
            {s.done ? '\u2713' : i + 1}
          </div>
          <div style={{ marginLeft: 10 }}>
            <div style={{
              fontSize: 13, fontWeight: 700,
              color: s.done ? 'var(--t-accent)' : 'var(--t-text-bright)',
            }}>{s.label}</div>
            <div style={{ fontSize: 11, color: 'var(--t-text-muted)', marginTop: 1 }}>
              {s.desc}
            </div>
          </div>
          {i < steps.length - 1 && (
            <div style={{
              flex: 1, height: 2, marginLeft: 16, marginRight: 12,
              background: s.done ? 'var(--t-accent)' : 'var(--t-border)',
              borderRadius: 1,
            }} />
          )}
        </div>
      ))}
    </div>
  );
}


// ── Main component ───────────────────────────────────────

export function PhoneSettings() {
  // Tenant-level state
  const [config, setConfig]         = useState<PhoneConfig | null>(null);
  const [defaults, setDefaults]     = useState<Record<string, any>>({});
  const [webhooks, setWebhooks]     = useState<Record<string, string>>({});
  const [credMode, setCredMode]     = useState<'platform' | 'byok'>('platform');

  // Agent state
  const [agents, setAgents]           = useState<PhoneAgent[]>([]);
  const [selectedId, setSelectedId]   = useState<number | null>(null);
  const [agentForm, setAgentForm]     = useState<Record<string, any>>({});
  const [showPromptEditor, setShowPromptEditor] = useState(false);
  const [defaultPrompt, setDefaultPrompt]       = useState('');

  // Call logs
  const [sessions, setSessions]     = useState<PhoneSession[]>([]);

  // Messaging state
  const [msgConfig, setMsgConfig]           = useState<MessagingConfig | null>(null);
  const [msgWebhooks, setMsgWebhooks]       = useState<Record<string, string>>({});
  const [conversations, setConversations]   = useState<MessagingConversation[]>([]);
  const [selectedConvId, setSelectedConvId] = useState<number | null>(null);
  const [messages, setMessages]             = useState<Message[]>([]);
  const [msgStats, setMsgStats]             = useState<MessagingStats | null>(null);
  const [templates, setTemplates]           = useState<MessagingTemplate[]>([]);
  const [msgReply, setMsgReply]             = useState('');
  const [sendingMsg, setSendingMsg]         = useState(false);
  const [savingMsgConfig, setSavingMsgConfig] = useState(false);
  const [channelFilter, setChannelFilter]   = useState<'' | 'sms' | 'whatsapp'>('');
  const [msgConfigForm, setMsgConfigForm]   = useState({
    sms_enabled: false, whatsapp_enabled: false,
    whatsapp_phone_number: '', whatsapp_status: 'not_configured',
    auto_reply_enabled: false, auto_reply_message: '',
    auto_create_ticket: false, default_language: 'en',
  });
  const [showTemplateForm, setShowTemplateForm] = useState(false);
  const [templateForm, setTemplateForm]     = useState({ name: '', body: '', language: 'en', category: 'utility' });

  // UI state
  const [activeTab, setActiveTab]   = useState<'agents' | 'logs' | 'messaging'>('agents');
  const [loading, setLoading]       = useState(true);
  const [saving, setSaving]         = useState(false);
  const [deploying, setDeploying]   = useState(false);
  const [activating, setActivating] = useState(false);
  const [error, setError]           = useState('');
  const [success, setSuccess]       = useState('');
  const [showNewAgent, setShowNewAgent]     = useState(false);
  const [newAgentName, setNewAgentName]     = useState('');
  const [newAgentLang, setNewAgentLang]     = useState('en');
  const [creating, setCreating]     = useState(false);
  const [deleting, setDeleting]     = useState(false);
  const [llmSelectValue, setLlmSelectValue] = useState(LLM_MODEL_OPTIONS[0].value);

  // Credential form (tenant-level)
  const [credForm, setCredForm] = useState({
    twilio_account_sid: '', twilio_auth_token: '',
    twilio_phone_number: '', elevenlabs_api_key: '',
  });
  const [savingCreds, setSavingCreds] = useState(false);
  const [enabling, setEnabling]       = useState(false);

  // IVR greeting now lives on each agent (ivr_greeting field)

  const selectedAgent = agents.find(a => a.id === selectedId) || null;

  // ── Load on mount ──
  useEffect(() => { loadAll(); }, []);
  useEffect(() => { if (activeTab === 'logs') loadSessions(); }, [activeTab, selectedId]);
  useEffect(() => { if (activeTab === 'messaging') loadMessaging(); }, [activeTab]);
  useEffect(() => { if (activeTab === 'messaging') loadConversations(); }, [activeTab, channelFilter]);
  useEffect(() => { if (selectedConvId) loadMessages(selectedConvId); }, [selectedConvId]);

  async function loadAll() {
    setLoading(true);
    try {
      const [configData, agentData, defaultsData, webhookData] = await Promise.all([
        api.getPhoneConfig().catch(() => ({ configured: false, is_active: false })),
        api.listPhoneAgents().catch(() => []),
        api.getPhoneDefaults().catch(() => ({})),
        api.getPhoneWebhooks().catch(() => ({})),
      ]);
      setConfig(configData.configured ? configData : { configured: false, is_active: false });
      setCredMode(configData.credentials_mode || 'platform');
      setAgents(Array.isArray(agentData) ? agentData : []);
      setDefaults(defaultsData);
      setWebhooks(webhookData);

      // Auto-select first agent
      if (Array.isArray(agentData) && agentData.length > 0 && !selectedId) {
        selectAgent(agentData[0]);
      }
    } catch {
      setError('Failed to load phone settings');
    } finally {
      setLoading(false);
    }
  }

  async function loadAgents() {
    try {
      const data = await api.listPhoneAgents();
      setAgents(Array.isArray(data) ? data : []);
    } catch { /* non-fatal */ }
  }

  // ── Messaging functions ──

  async function loadMessaging() {
    try {
      const [configData, webhookData, statsData, templateData] = await Promise.all([
        api.getMessagingConfig().catch(() => null),
        api.getMessagingWebhooks().catch(() => ({})),
        api.getMessagingStats().catch(() => null),
        api.listMessagingTemplates().catch(() => []),
      ]);
      if (configData) {
        setMsgConfig(configData);
        setMsgConfigForm({
          sms_enabled: configData.sms_enabled || false,
          whatsapp_enabled: configData.whatsapp_enabled || false,
          whatsapp_phone_number: configData.whatsapp_phone_number || '',
          whatsapp_status: configData.whatsapp_status || 'not_configured',
          auto_reply_enabled: configData.auto_reply_enabled || false,
          auto_reply_message: configData.auto_reply_message || '',
          auto_create_ticket: configData.auto_create_ticket || false,
          default_language: configData.default_language || 'en',
        });
      }
      setMsgWebhooks(webhookData);
      setMsgStats(statsData);
      setTemplates(Array.isArray(templateData) ? templateData : []);
    } catch { /* non-fatal */ }
  }

  async function loadConversations() {
    try {
      const data = await api.listMsgConversations(channelFilter || undefined, 'active');
      setConversations(Array.isArray(data) ? data : []);
    } catch { /* non-fatal */ }
  }

  async function loadMessages(convId: number) {
    try {
      const data = await api.getMsgMessages(convId);
      setMessages(Array.isArray(data) ? data : []);
    } catch { /* non-fatal */ }
  }

  async function handleSaveMsgConfig() {
    setSavingMsgConfig(true);
    setError(''); setSuccess('');
    try {
      const updated = await api.saveMessagingConfig(msgConfigForm);
      setMsgConfig(updated);
      setSuccess('Messaging settings saved');
    } catch (e: any) {
      setError(e?.message || 'Failed to save messaging settings');
    } finally {
      setSavingMsgConfig(false);
    }
  }

  async function handleSendMessage() {
    if (!selectedConvId || !msgReply.trim()) return;
    setSendingMsg(true);
    try {
      await api.sendMsgMessage(selectedConvId, msgReply.trim());
      setMsgReply('');
      await loadMessages(selectedConvId);
      await loadConversations();
    } catch (e: any) {
      setError(e?.message || 'Failed to send message');
    } finally {
      setSendingMsg(false);
    }
  }

  async function handleCreateTemplate() {
    setError(''); setSuccess('');
    try {
      await api.createMessagingTemplate(templateForm);
      setTemplateForm({ name: '', body: '', language: 'en', category: 'utility' });
      setShowTemplateForm(false);
      await loadMessaging();
      setSuccess('Template created');
    } catch (e: any) {
      setError(e?.message || 'Failed to create template');
    }
  }

  async function handleDeleteTemplate(id: number) {
    try {
      await api.deleteMessagingTemplate(id);
      await loadMessaging();
    } catch { /* non-fatal */ }
  }

  async function loadSessions() {
    try {
      const data = await api.listPhoneSessions(50, selectedId || undefined);
      setSessions(Array.isArray(data) ? data : []);
    } catch { /* non-fatal */ }
  }

  function selectAgent(agent: PhoneAgent) {
    setSelectedId(agent.id);
    const rawModel = agent.llm_model || '';
    setLlmSelectValue(resolveModelSelectValue(rawModel));
    setShowPromptEditor(false);
    setAgentForm({
      name:             agent.name || '',
      voice_id:         agent.voice_id || '',
      greeting_message: agent.greeting_message || '',
      ivr_greeting:     agent.ivr_greeting || '',
      system_prompt:    agent.system_prompt || '',
      llm_model:        rawModel,
      llm_model_custom: resolveModelSelectValue(rawModel) === '__custom__' ? rawModel : '',
      temperature:      agent.temperature != null ? String(agent.temperature) : '',
      turn_timeout:     agent.turn_timeout != null ? String(agent.turn_timeout) : '',
      audio_format:     agent.audio_format || '',
      tts_speed:        agent.tts_speed != null ? String(agent.tts_speed) : '',
      ivr_digit:        agent.ivr_digit || '',
      oncall_number:    agent.oncall_number || '',
      tools_enabled:    agent.tools_enabled || ALL_TOOLS.map(t => t.key),
    });
    setError(''); setSuccess('');
  }

  // ── Load full agent details (including system_prompt) ──
  async function loadFullAgent(agentId: number) {
    try {
      const full = await api.getPhoneAgent(agentId);
      if (full) {
        // Update in agents list
        setAgents(prev => prev.map(a => a.id === agentId ? { ...a, ...full } : a));
        selectAgent(full);
      }
    } catch { /* non-fatal */ }
  }

  function setField(field: string, value: any) {
    setAgentForm(f => ({ ...f, [field]: value }));
    setError(''); setSuccess('');
  }

  // ── Agent CRUD ──

  async function handleCreateAgent() {
    if (!newAgentName.trim()) return;
    setCreating(true); setError('');
    try {
      const agent = await api.createPhoneAgent({
        name: newAgentName.trim(),
        language: newAgentLang,
      });
      await loadAgents();
      selectAgent(agent);
      setShowNewAgent(false);
      setNewAgentName('');
      setSuccess(`Agent "${agent.name}" created.`);
    } catch (e: any) {
      setError(e?.error || e?.message || 'Failed to create agent');
    } finally {
      setCreating(false);
    }
  }

  async function handleSaveAgent() {
    if (!selectedId) return;
    setSaving(true); setError(''); setSuccess('');
    try {
      const resolvedModel = llmSelectValue === '__custom__'
        ? agentForm.llm_model_custom || null
        : (agentForm.llm_model || null);

      const payload: Record<string, any> = {
        name:             agentForm.name,
        voice_id:         agentForm.voice_id || null,
        greeting_message: agentForm.greeting_message || null,
        ivr_greeting:     agentForm.ivr_greeting || null,
        system_prompt:    agentForm.system_prompt || null,
        llm_model:        resolvedModel,
        temperature:      agentForm.temperature !== '' ? parseFloat(agentForm.temperature) : null,
        turn_timeout:     agentForm.turn_timeout !== '' ? parseFloat(agentForm.turn_timeout) : null,
        audio_format:     agentForm.audio_format || null,
        tts_speed:        agentForm.tts_speed !== '' ? parseFloat(agentForm.tts_speed) : null,
        ivr_digit:        agentForm.ivr_digit || null,
        oncall_number:    agentForm.oncall_number || null,
        tools_enabled:    agentForm.tools_enabled,
      };
      const updated = await api.updatePhoneAgent(selectedId, payload);
      await loadAgents();
      selectAgent(updated);
      setSuccess('Agent settings saved.');
    } catch (e: any) {
      setError(e?.error || e?.message || 'Save failed');
    } finally {
      setSaving(false);
    }
  }

  async function handleDeployAgent() {
    if (!selectedId) return;
    setDeploying(true); setError(''); setSuccess('');
    try {
      await api.deployPhoneAgent(selectedId);
      await loadAgents();
      if (selectedId) await loadFullAgent(selectedId);
      setSuccess('Agent deployed to ElevenLabs.');
    } catch (e: any) {
      setError(e?.error || e?.message || 'Deploy failed');
    } finally {
      setDeploying(false);
    }
  }

  async function handleActivateAgent() {
    if (!selectedId) return;
    setActivating(true); setError(''); setSuccess('');
    try {
      await api.activatePhoneAgent(selectedId);
      await loadAgents();
      if (selectedId) await loadFullAgent(selectedId);
      setSuccess('Agent activated and linked to phone number.');
    } catch (e: any) {
      setError(e?.error || e?.message || 'Activation failed');
    } finally {
      setActivating(false);
    }
  }

  async function handleResetAgent() {
    if (!selectedId) return;
    setError(''); setSuccess('');
    try {
      const reset = await api.resetPhoneAgent(selectedId);
      await loadAgents();
      selectAgent(reset);
      setSuccess('Agent reset to platform defaults.');
    } catch (e: any) {
      setError(e?.error || e?.message || 'Reset failed');
    }
  }

  async function handleDeleteAgent() {
    if (!selectedId || !selectedAgent) return;
    if (!window.confirm(`Delete agent "${selectedAgent.name}"? This will deprovision it from ElevenLabs.`)) return;
    setDeleting(true); setError('');
    try {
      await api.deletePhoneAgent(selectedId);
      setSelectedId(null);
      await loadAgents();
      setSuccess('Agent deleted.');
    } catch (e: any) {
      setError(e?.error || e?.message || 'Delete failed');
    } finally {
      setDeleting(false);
    }
  }

  // ── Prompt editor helpers ──

  async function handleViewDefaultPrompt() {
    if (!selectedAgent) return;
    try {
      const data = await api.getDefaultPrompt(selectedAgent.language, agentForm.name || selectedAgent.name);
      setDefaultPrompt(data.prompt);
      setShowPromptEditor(true);
    } catch {
      setError('Failed to load default prompt');
    }
  }

  // ── Credential save (tenant-level) ──

  async function handleSaveCreds() {
    setSavingCreds(true); setError(''); setSuccess('');
    try {
      const payload: Record<string, any> = { credentials_mode: credMode };
      if (credMode === 'byok') {
        if (credForm.twilio_account_sid)  payload.twilio_account_sid  = credForm.twilio_account_sid;
        if (credForm.twilio_auth_token)   payload.twilio_auth_token   = credForm.twilio_auth_token;
        if (credForm.twilio_phone_number) payload.twilio_phone_number = credForm.twilio_phone_number;
        if (credForm.elevenlabs_api_key)  payload.elevenlabs_api_key  = credForm.elevenlabs_api_key;
      }
      await api.savePhoneConfig(payload);
      setSuccess('Credentials saved.');
      setCredForm({ twilio_account_sid: '', twilio_auth_token: '', twilio_phone_number: '', elevenlabs_api_key: '' });
    } catch (e: any) {
      setError(e?.message || 'Save failed');
    } finally {
      setSavingCreds(false);
    }
  }

  // handleSaveIvr removed — IVR greeting now saved per-agent via handleSaveAgent

  async function handleEnablePlatform() {
    setEnabling(true); setError(''); setSuccess('');
    try {
      const result = await api.enablePhone();
      setSuccess(`Platform phone enabled! Number: ${result.phone_number}`);
      await loadAll();
    } catch (e: any) {
      setError(e?.error || e?.message || 'Enable failed');
    } finally {
      setEnabling(false);
    }
  }

  // ── LLM select handler ──
  function handleLlmSelectChange(val: string) {
    setLlmSelectValue(val);
    if (val === '__custom__') {
      setField('llm_model', agentForm.llm_model_custom);
    } else {
      setField('llm_model', val);
      setField('llm_model_custom', '');
    }
  }

  if (loading) {
    return <div style={{ padding: 32, color: 'var(--t-text-muted)' }}>Loading phone settings...</div>;
  }

  const isPlatform    = credMode === 'platform';
  const effectiveNumber = config?.effective_phone_number || config?.assigned_phone_number;
  const hasNumber     = Boolean(effectiveNumber);
  const hasByokCreds  = Boolean(config?.elevenlabs_api_key_set && config?.twilio_auth_token_set);

  const tempValue      = agentForm.temperature  !== '' ? parseFloat(agentForm.temperature)  : (defaults.temperature  ?? 0.7);
  const speedValue     = agentForm.tts_speed    !== '' ? parseFloat(agentForm.tts_speed)    : (defaults.tts_speed    ?? 1.15);

  return (
    <div>

      {/* Header + Tabs */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
        <div>
          <h2 style={{ margin: '0 0 4px', fontSize: 20, color: 'var(--t-text-bright)' }}>Communications</h2>
          <p style={{ margin: 0, color: 'var(--t-text-muted)', fontSize: 13 }}>
            Voice agents, SMS, WhatsApp, and call logs.
            {effectiveNumber && (
              <span style={{ fontFamily: 'monospace', color: 'var(--t-accent)', fontWeight: 600, marginLeft: 10 }}>
                {effectiveNumber}
              </span>
            )}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {([
            { key: 'agents' as const, label: 'Voice Agents' },
            { key: 'messaging' as const, label: 'Messaging' },
            { key: 'logs' as const, label: 'Call Logs' },
          ]).map((t) => (
            <button key={t.key} className={`btn ${activeTab === t.key ? 'btn-primary' : 'btn-ghost'}`}
              style={{ fontSize: 13 }} onClick={() => setActiveTab(t.key)}>
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {error   && <div className="alert alert-error"   style={{ marginBottom: 16 }} role="alert">{error}</div>}
      {success && <div className="alert alert-success" style={{ marginBottom: 16 }} role="status">{success}</div>}

      {/* ══════════════════════════════════════════════════════
          AGENTS TAB — Two-panel manila folder layout
          ══════════════════════════════════════════════════════ */}
      {activeTab === 'agents' && (
        <div style={{ display: 'flex', gap: 20, minHeight: 600 }}>

          {/* ── Left Panel: Agent list + Tenant config ── */}
          <div style={{ width: 240, flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 12 }}>

            {/* Agent cards */}
            <div style={{
              background: 'var(--t-panel)', borderRadius: 8, padding: 12,
              border: '1px solid var(--t-border)',
              display: 'flex', flexDirection: 'column', gap: 6,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                <span style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--t-text-muted)' }}>
                  Agents
                </span>
                <span style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>{agents.length}</span>
              </div>

              {agents.map(agent => {
                const selected = selectedId === agent.id;
                const statusColor = agent.is_active ? 'var(--t-accent)' : agent.is_deployed ? 'var(--t-warning)' : 'var(--t-text-muted)';
                const statusLabel = agent.is_active ? 'Active' : agent.is_deployed ? 'Deployed' : 'Draft';
                return (
                  <button key={agent.id}
                    onClick={() => { loadFullAgent(agent.id); }}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 10,
                      padding: '10px 12px', borderRadius: 6,
                      cursor: 'pointer', width: '100%', textAlign: 'left',
                      background: selected ? 'var(--t-accent-bg)' : 'var(--t-panel-alt)',
                      border: selected ? '1px solid var(--t-accent-border)' : '1px solid var(--t-border)',
                      borderLeft: selected ? '3px solid var(--t-accent)' : '3px solid transparent',
                      transition: 'all 0.15s',
                    }}>
                    <span style={{
                      fontSize: 10, fontWeight: 700, padding: '3px 6px', borderRadius: 3,
                      background: 'var(--t-input-bg, rgba(255,255,255,0.06))', color: 'var(--t-text-muted)',
                      letterSpacing: '0.04em',
                    }}>
                      {LANG_FLAGS[agent.language] || agent.language.toUpperCase()}
                    </span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{
                        fontSize: 13, fontWeight: 600,
                        color: 'var(--t-text-bright)',
                        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                      }}>{agent.name}</div>
                      <div style={{ fontSize: 11, color: statusColor, fontWeight: 500 }}>
                        {statusLabel}
                      </div>
                    </div>
                    <span style={{
                      width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
                      background: statusColor,
                      boxShadow: agent.is_active ? '0 0 6px var(--t-accent-border)' : 'none',
                    }} />
                  </button>
                );
              })}

              {/* New Agent */}
              {showNewAgent ? (
                <div style={{ padding: '8px 4px', display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <input className="form-input" placeholder="Agent name" value={newAgentName}
                    onChange={e => setNewAgentName(e.target.value)} autoFocus
                    style={{ fontSize: 13 }} />
                  <select className="form-input" value={newAgentLang}
                    onChange={e => setNewAgentLang(e.target.value)} style={{ fontSize: 13 }}>
                    {Object.entries(LANG_LABELS).map(([k, v]) => (
                      <option key={k} value={k}>{v}</option>
                    ))}
                  </select>
                  <div style={{ display: 'flex', gap: 6 }}>
                    <button className="btn btn-primary" style={{ fontSize: 12, flex: 1 }}
                      onClick={handleCreateAgent} disabled={creating || !newAgentName.trim()}>
                      {creating ? 'Creating...' : 'Create'}
                    </button>
                    <button className="btn btn-ghost" style={{ fontSize: 12 }}
                      onClick={() => { setShowNewAgent(false); setNewAgentName(''); }}>
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <button className="btn btn-ghost" style={{ fontSize: 12, width: '100%', marginTop: 4 }}
                  onClick={() => setShowNewAgent(true)}>
                  + New Agent
                </button>
              )}
            </div>

            {/* Tenant-level settings */}
            <div style={{
              background: 'var(--t-panel)', borderRadius: 8, padding: 12,
              border: '1px solid var(--t-border)',
              display: 'flex', flexDirection: 'column', gap: 10,
            }}>
              <span style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--t-text-muted)' }}>
                Tenant Settings
              </span>

              {/* Credential mode */}
              <div style={{ display: 'flex', gap: 4 }}>
                <button className={`btn ${isPlatform ? 'btn-primary' : 'btn-ghost'}`}
                  style={{ fontSize: 11, padding: '4px 10px', flex: 1 }}
                  onClick={() => setCredMode('platform')}>Platform</button>
                <button className={`btn ${!isPlatform ? 'btn-primary' : 'btn-ghost'}`}
                  style={{ fontSize: 11, padding: '4px 10px', flex: 1 }}
                  onClick={() => setCredMode('byok')}>BYOK</button>
              </div>

              {isPlatform ? (
                <div style={{ fontSize: 12 }}>
                  {hasNumber ? (
                    <div style={{
                      padding: '8px 10px', borderRadius: 6,
                      background: 'var(--t-accent-bg)',
                      border: '1px solid var(--t-accent-border)',
                    }}>
                      <div style={{ fontSize: 11, color: 'var(--t-text-muted)', marginBottom: 2 }}>Phone Number</div>
                      <div style={{ fontFamily: 'monospace', fontSize: 14, fontWeight: 700, color: 'var(--t-accent)' }}>
                        {effectiveNumber}
                      </div>
                    </div>
                  ) : (
                    <button className="btn btn-primary" style={{ fontSize: 12, width: '100%' }}
                      onClick={handleEnablePlatform} disabled={enabling}>
                      {enabling ? 'Purchasing...' : 'Purchase Twilio Number'}
                    </button>
                  )}
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  <input className="form-input" type="password" placeholder="Twilio SID" style={{ fontSize: 12 }}
                    value={credForm.twilio_account_sid} onChange={e => setCredForm(f => ({ ...f, twilio_account_sid: e.target.value }))} />
                  <input className="form-input" type="password" placeholder="Twilio Token" style={{ fontSize: 12 }}
                    value={credForm.twilio_auth_token} onChange={e => setCredForm(f => ({ ...f, twilio_auth_token: e.target.value }))} />
                  <input className="form-input" type="text" placeholder="+14155552671" style={{ fontSize: 12 }}
                    value={credForm.twilio_phone_number} onChange={e => setCredForm(f => ({ ...f, twilio_phone_number: e.target.value }))} />
                  <input className="form-input" type="password" placeholder="ElevenLabs API Key" style={{ fontSize: 12 }}
                    value={credForm.elevenlabs_api_key} onChange={e => setCredForm(f => ({ ...f, elevenlabs_api_key: e.target.value }))} />
                  <button className="btn btn-primary" style={{ fontSize: 12 }}
                    onClick={handleSaveCreds} disabled={savingCreds}>
                    {savingCreds ? 'Saving...' : 'Save Credentials'}
                  </button>
                  {hasByokCreds && (
                    <span style={{ fontSize: 11, color: 'var(--t-accent)' }}>Credentials configured</span>
                  )}
                </div>
              )}

              {/* IVR greeting moved to per-agent Routing & Transfer section */}
            </div>
          </div>

          {/* ── Right Panel: Selected agent detail ── */}
          <div style={{ flex: 1, minWidth: 0 }}>
            {!selectedAgent ? (
              <div style={{
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                height: 400, color: 'var(--t-text-muted)', fontSize: 14,
                background: 'var(--t-panel)', borderRadius: 8,
                border: '1px solid var(--t-border)',
              }}>
                {agents.length === 0
                  ? 'Create your first agent to get started.'
                  : 'Select an agent from the list.'}
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

                {/* Deploy Stepper */}
                <DeployStepper agent={selectedAgent} />

                {/* ── Persona Section ── */}
                <section style={{ background: 'var(--t-panel)', borderRadius: 8, padding: 20, border: '1px solid var(--t-border)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
                    <h3 style={{ margin: 0, fontSize: 15, color: 'var(--t-text-bright)' }}>
                      Persona
                      <span style={{ fontSize: 12, color: 'var(--t-text-muted)', fontWeight: 400, marginLeft: 8 }}>
                        {LANG_LABELS[selectedAgent.language] || selectedAgent.language}
                      </span>
                    </h3>
                  </div>

                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                    <div>
                      <label className="form-label">Agent Name</label>
                      <input className="form-input" value={agentForm.name || ''}
                        onChange={e => setField('name', e.target.value)} placeholder="Atlas" />
                    </div>
                    <div>
                      <label className="form-label">
                        ElevenLabs Voice ID
                        <span style={{ color: 'var(--t-text-muted)', fontWeight: 400, marginLeft: 6, fontSize: 11 }}>from EL dashboard</span>
                      </label>
                      <input className="form-input" value={agentForm.voice_id || ''}
                        onChange={e => setField('voice_id', e.target.value)} />
                    </div>
                    <div style={{ gridColumn: '1 / -1' }}>
                      <label className="form-label">
                        Custom Greeting <span style={{ color: 'var(--t-text-muted)', fontWeight: 400, marginLeft: 6 }}>optional</span>
                      </label>
                      <input className="form-input" value={agentForm.greeting_message || ''}
                        onChange={e => setField('greeting_message', e.target.value)}
                        placeholder={`Hello! This is ${agentForm.name || 'Atlas'}, your IT support specialist. How can I help?`} />
                    </div>
                  </div>

                  {/* System Prompt Override */}
                  <div style={{ marginTop: 16 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
                      <label className="form-label" style={{ margin: 0 }}>System Prompt</label>
                      {agentForm.system_prompt ? (
                        <span style={{
                          fontSize: 11, padding: '2px 8px', borderRadius: 4,
                          background: 'rgba(255, 68, 68, 0.1)', color: 'var(--t-error)', fontWeight: 600,
                        }}>Custom Override</span>
                      ) : (
                        <span style={{
                          fontSize: 11, padding: '2px 8px', borderRadius: 4,
                          background: 'rgba(52, 211, 153, 0.15)', color: 'var(--t-accent)', fontWeight: 600,
                        }}>Platform Default</span>
                      )}
                    </div>

                    {!showPromptEditor ? (
                      <div style={{ display: 'flex', gap: 8 }}>
                        {!agentForm.system_prompt ? (
                          <button className="btn btn-ghost" style={{ fontSize: 12 }}
                            onClick={() => { setField('system_prompt', ' '); setShowPromptEditor(true); }}>
                            Override with Custom Prompt
                          </button>
                        ) : (
                          <>
                            <button className="btn btn-ghost" style={{ fontSize: 12 }}
                              onClick={() => setShowPromptEditor(true)}>
                              Edit Custom Prompt
                            </button>
                            <button className="btn btn-ghost" style={{ fontSize: 12, color: 'var(--t-accent)' }}
                              onClick={() => { setField('system_prompt', ''); setShowPromptEditor(false); }}>
                              Revert to Default
                            </button>
                          </>
                        )}
                      </div>
                    ) : (
                      <div>
                        {/* Red alert box */}
                        <div style={{
                          padding: '10px 14px', borderRadius: 6, marginBottom: 10,
                          background: 'rgba(255, 68, 68, 0.08)',
                          border: '1px solid rgba(255, 68, 68, 0.3)',
                          color: 'var(--t-error)', fontSize: 13,
                        }}>
                          <strong>Warning:</strong> You are overriding the platform's built-in system prompt.
                          This replaces all default safety rules, tool instructions, and persona configuration.
                          If your agent behaves unexpectedly, click "Revert to Default" to restore the platform prompt.
                        </div>

                        <textarea value={agentForm.system_prompt?.trim() === '' ? '' : agentForm.system_prompt}
                          onChange={e => setField('system_prompt', e.target.value)}
                          placeholder="Enter your custom system prompt here..."
                          style={{
                            width: '100%', minHeight: 300, fontFamily: 'monospace', fontSize: 12,
                            background: 'var(--t-panel-alt)', color: 'var(--t-text-bright)',
                            border: '1px solid rgba(255, 68, 68, 0.2)', borderRadius: 6, padding: 10,
                            resize: 'vertical',
                          }} />
                        <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                          <button className="btn btn-ghost" style={{ fontSize: 12, color: 'var(--t-accent)' }}
                            onClick={() => { setField('system_prompt', ''); setShowPromptEditor(false); }}>
                            Revert to Default
                          </button>
                          <button className="btn btn-ghost" style={{ fontSize: 12 }}
                            onClick={() => setShowPromptEditor(false)}>
                            Close Editor
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                </section>

                {/* ── AI & Voice Settings ── */}
                <section style={{ background: 'var(--t-panel)', borderRadius: 8, padding: 20, border: '1px solid var(--t-border)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
                    <h3 style={{ margin: 0, fontSize: 15, color: 'var(--t-text-bright)' }}>AI & Voice Settings</h3>
                    <button className="btn btn-ghost" style={{ fontSize: 12 }}
                      onClick={handleResetAgent}>Reset to Defaults</button>
                  </div>

                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                    {/* LLM Model */}
                    <div style={{ gridColumn: '1 / -1' }}>
                      <label className="form-label">LLM Model</label>
                      <select className="form-input" value={llmSelectValue}
                        onChange={e => handleLlmSelectChange(e.target.value)}>
                        {LLM_MODEL_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                      </select>
                      {llmSelectValue === '__custom__' && (
                        <input className="form-input" style={{ marginTop: 8 }}
                          value={agentForm.llm_model_custom || ''} placeholder="e.g. claude-opus-4-20250101"
                          onChange={e => {
                            setField('llm_model_custom', e.target.value);
                            setField('llm_model', e.target.value);
                          }} />
                      )}
                      {defaults.llm_model && (
                        <span style={{ fontSize: 11, color: 'var(--t-text-muted)', marginTop: 4, display: 'block' }}>
                          Default: {defaults.llm_model}
                        </span>
                      )}
                    </div>

                    {/* Temperature */}
                    <div>
                      <label className="form-label">
                        Temperature
                        <span style={{ color: 'var(--t-text-bright)', fontWeight: 600, marginLeft: 8 }}>
                          {agentForm.temperature !== '' ? parseFloat(agentForm.temperature).toFixed(1) : '—'}
                        </span>
                      </label>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <span style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>0.0</span>
                        <input type="range" min="0" max="1" step="0.1"
                          value={agentForm.temperature !== '' ? agentForm.temperature : String(defaults.temperature ?? 0.7)}
                          onChange={e => setField('temperature', e.target.value)}
                          style={{ flex: 1, accentColor: 'var(--t-info)' }} />
                        <span style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>1.0</span>
                      </div>
                    </div>

                    {/* TTS Speed */}
                    <div>
                      <label className="form-label">
                        TTS Speed
                        <span style={{ color: 'var(--t-text-bright)', fontWeight: 600, marginLeft: 8 }}>
                          {agentForm.tts_speed ? `${agentForm.tts_speed}x` : `${defaults.tts_speed ?? 1.15}x`}
                        </span>
                      </label>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <span style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>0.5x</span>
                        <input type="range" min="0.5" max="2.0" step="0.05"
                          value={agentForm.tts_speed || defaults.tts_speed || 1.15}
                          onChange={e => setField('tts_speed', e.target.value)}
                          style={{ flex: 1, accentColor: 'var(--t-info)' }} />
                        <span style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>2.0x</span>
                      </div>
                    </div>

                    {/* Turn Timeout */}
                    <div>
                      <label className="form-label">Turn Timeout (seconds)</label>
                      <input type="number" className="form-input" min="1" max="30" step="0.5"
                        value={agentForm.turn_timeout}
                        placeholder={defaults.turn_timeout != null ? String(defaults.turn_timeout) : '10'}
                        onChange={e => setField('turn_timeout', e.target.value)} />
                    </div>

                    {/* Audio Format */}
                    <div>
                      <label className="form-label">Audio Format</label>
                      <select className="form-input" value={agentForm.audio_format || 'ulaw_8000'}
                        onChange={e => setField('audio_format', e.target.value)}>
                        {AUDIO_FORMAT_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                      </select>
                    </div>
                  </div>
                </section>

                {/* ── Routing & Transfer ── */}
                <section style={{ background: 'var(--t-panel)', borderRadius: 8, padding: 20, border: '1px solid var(--t-border)' }}>
                  <h3 style={{ margin: '0 0 14px', fontSize: 15, color: 'var(--t-text-bright)' }}>Routing & Transfer</h3>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                    <div>
                      <label className="form-label">
                        IVR Digit
                        <span style={{ color: 'var(--t-text-muted)', fontWeight: 400, marginLeft: 6, fontSize: 11 }}>
                          caller presses to reach this agent
                        </span>
                      </label>
                      <input className="form-input" value={agentForm.ivr_digit || ''}
                        onChange={e => setField('ivr_digit', e.target.value.slice(0, 1))}
                        placeholder="1" maxLength={1} style={{ width: 80 }} />
                    </div>
                    <div>
                      <label className="form-label">On-call Number (E.164)</label>
                      <input className="form-input" value={agentForm.oncall_number || ''}
                        onChange={e => setField('oncall_number', e.target.value)}
                        placeholder="+14155559876" />
                      <span style={{ fontSize: 11, color: 'var(--t-text-muted)', marginTop: 4, display: 'block' }}>
                        Rings for 30s when AI can't resolve. No answer = email fallback.
                      </span>
                    </div>
                  </div>

                  {/* IVR Greeting — per-agent */}
                  <div style={{ marginTop: 14 }}>
                    <label className="form-label">
                      IVR Greeting
                      <span style={{ color: 'var(--t-text-muted)', fontWeight: 400, marginLeft: 6, fontSize: 11 }}>
                        spoken to caller via Twilio before connecting
                      </span>
                    </label>
                    <textarea className="form-input" rows={2}
                      style={{ fontSize: 12, resize: 'vertical' }}
                      value={agentForm.ivr_greeting || ''}
                      onChange={e => setField('ivr_greeting', e.target.value)}
                      placeholder={selectedAgent?.language === 'es'
                        ? `Oprima ${agentForm.ivr_digit || '?'} para soporte en español.`
                        : selectedAgent?.language === 'fr'
                        ? `Appuyez sur ${agentForm.ivr_digit || '?'} pour le support en français.`
                        : `Press ${agentForm.ivr_digit || '?'} for support in English.`
                      } />
                    <span style={{ fontSize: 11, color: 'var(--t-text-muted)', marginTop: 4, display: 'block' }}>
                      Leave blank for auto-generated default based on language. Played via Twilio Polly voice in IVR menu.
                    </span>
                  </div>

                  {/* Tool toggles — full-width grid */}
                  <div style={{ marginTop: 20 }}>
                    <label className="form-label" style={{ marginBottom: 10, display: 'block' }}>Tools Enabled</label>
                    <div style={{
                      display: 'grid',
                      gridTemplateColumns: `repeat(${ALL_TOOLS.length}, 1fr)`,
                      gap: 8,
                    }}>
                      {ALL_TOOLS.map(tool => {
                        const enabled = (agentForm.tools_enabled || []).includes(tool.key);
                        return (
                          <label key={tool.key} style={{
                            display: 'flex', alignItems: 'center', gap: 10,
                            padding: '10px 14px', borderRadius: 6, cursor: 'pointer',
                            background: enabled ? 'var(--t-accent-bg)' : 'var(--t-panel-alt)',
                            border: enabled ? '1px solid var(--t-accent-border)' : '1px solid var(--t-border)',
                            color: enabled ? 'var(--t-text-bright)' : 'var(--t-text-muted)',
                            fontSize: 13, fontWeight: 500,
                            transition: 'all 0.15s',
                          }}>
                            <input type="checkbox" checked={enabled}
                              onChange={() => {
                                const current = agentForm.tools_enabled || [];
                                setField('tools_enabled',
                                  enabled ? current.filter((k: string) => k !== tool.key) : [...current, tool.key]
                                );
                              }}
                              style={{ accentColor: 'var(--t-accent)', width: 16, height: 16 }} />
                            {tool.label}
                          </label>
                        );
                      })}
                    </div>
                  </div>
                </section>

                {/* ── Webhooks (read-only) ── */}
                {Object.keys(webhooks).length > 0 && (
                  <section style={{ background: 'var(--t-panel)', borderRadius: 8, padding: 20, border: '1px solid var(--t-border)' }}>
                    <h3 style={{ margin: '0 0 4px', fontSize: 15, color: 'var(--t-text-bright)' }}>Webhooks</h3>
                    <p style={{ margin: '0 0 16px', fontSize: 13, color: 'var(--t-text-muted)' }}>
                      Read-only URLs your ElevenLabs agent uses. Auto-configured on deploy.
                    </p>
                    <div style={{
                      display: 'grid',
                      gridTemplateColumns: 'repeat(auto-fill, minmax(420px, 1fr))',
                      gap: 10,
                    }}>
                      {Object.entries(webhooks).filter(([, url]) => url).map(([key, url]) => (
                        <div key={key} style={{
                          padding: '10px 14px', borderRadius: 6,
                          background: 'var(--t-panel-alt)',
                          border: '1px solid var(--t-border)',
                        }}>
                          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                            <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--t-text-muted)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                              {WEBHOOK_LABELS[key] || key}
                            </span>
                            <CopyButton value={url} />
                          </div>
                          <div style={{
                            fontFamily: 'monospace', fontSize: 11, color: 'var(--t-text-bright)',
                            wordBreak: 'break-all', lineHeight: 1.4,
                          }}>
                            {url}
                          </div>
                        </div>
                      ))}
                    </div>
                  </section>
                )}

                {/* ── Action Buttons ── */}
                <div style={{
                  display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center',
                  padding: 16, borderRadius: 8,
                  background: 'var(--t-panel)', border: '1px solid var(--t-border)',
                }}>
                  <button className="btn btn-primary" onClick={handleSaveAgent} disabled={saving}>
                    {saving ? 'Saving...' : 'Save Settings'}
                  </button>

                  {!selectedAgent.is_deployed ? (
                    <button className="btn btn-secondary" onClick={handleDeployAgent} disabled={deploying}>
                      {deploying ? 'Deploying...' : 'Deploy to ElevenLabs'}
                    </button>
                  ) : !selectedAgent.is_active ? (
                    <button className="btn btn-secondary" onClick={handleActivateAgent} disabled={activating}>
                      {activating ? 'Activating...' : 'Activate Agent'}
                    </button>
                  ) : (
                    <>
                      <button className="btn btn-ghost" onClick={handleDeployAgent} disabled={deploying}>
                        {deploying ? 'Re-deploying...' : 'Re-deploy'}
                      </button>
                      <button className="btn btn-ghost" onClick={handleActivateAgent} disabled={activating}
                        title="Re-run IVR webhook setup — use if Twilio routing breaks">
                        {activating ? 'Re-linking...' : 'Re-link IVR'}
                      </button>
                    </>
                  )}

                  <div style={{ flex: 1 }} />

                  <button className="btn btn-ghost" style={{ color: 'var(--t-error)', fontSize: 12 }}
                    onClick={handleDeleteAgent} disabled={deleting}>
                    {deleting ? 'Deleting...' : 'Delete Agent'}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ══════════════════════════════════════════════════════
          CALL LOGS TAB
          ══════════════════════════════════════════════════════ */}
      {activeTab === 'logs' && (
        <div>
          {/* Agent filter */}
          {agents.length > 1 && (
            <div style={{ display: 'flex', gap: 6, marginBottom: 16 }}>
              <button className={`btn ${!selectedId ? 'btn-primary' : 'btn-ghost'}`}
                style={{ fontSize: 12 }} onClick={() => { setSelectedId(null); }}>
                All
              </button>
              {agents.map(a => (
                <button key={a.id}
                  className={`btn ${selectedId === a.id ? 'btn-primary' : 'btn-ghost'}`}
                  style={{ fontSize: 12 }}
                  onClick={() => setSelectedId(a.id)}>
                  {a.name}
                </button>
              ))}
            </div>
          )}

          {sessions.length === 0 ? (
            <div style={{ color: 'var(--t-text-muted)', padding: '32px 0', textAlign: 'center', fontSize: 14 }}>
              No calls yet. Once your phone line is active, calls will appear here.
            </div>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--t-border)' }}>
                  {['Time', 'Agent', 'Caller', 'Duration', 'Status', 'Transfer', 'Ticket', 'Cost'].map(h => (
                    <th key={h} style={{ padding: '8px 12px', textAlign: 'left', color: 'var(--t-text-muted)', fontWeight: 500 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sessions.map(s => (
                  <tr key={s.id} style={{ borderBottom: '1px solid var(--t-border)' }}>
                    <td style={{ padding: '10px 12px', color: 'var(--t-text-muted)' }}>
                      {new Date(s.started_at).toLocaleString()}
                    </td>
                    <td style={{ padding: '10px 12px', color: 'var(--t-text-muted)', fontSize: 12 }}>
                      {s.agent_name || '—'}
                    </td>
                    <td style={{ padding: '10px 12px' }}>
                      <div style={{ color: 'var(--t-text-bright)' }}>{s.caller_phone || '—'}</div>
                      {s.caller_email && <div style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>{s.caller_email}</div>}
                    </td>
                    <td style={{ padding: '10px 12px', color: 'var(--t-text-muted)' }}>
                      {formatDuration(s.duration_seconds)}
                    </td>
                    <td style={{ padding: '10px 12px' }}>
                      <span style={{
                        padding: '2px 8px', borderRadius: 4, fontSize: 11, fontWeight: 600,
                        background: `${STATUS_COLORS[s.status] || 'var(--t-text-muted)'}22`,
                        color: STATUS_COLORS[s.status] || 'var(--t-text-muted)',
                      }}>
                        {STATUS_LABELS[s.status] || s.status}
                      </span>
                    </td>
                    <td style={{ padding: '10px 12px' }}>
                      {s.transfer_attempted ? (
                        <span style={{ fontSize: 11, color: s.transfer_succeeded ? 'var(--t-accent)' : 'var(--t-warning)' }}>
                          {s.transfer_succeeded ? 'Connected' : 'No answer'}
                        </span>
                      ) : <span style={{ color: 'var(--t-text-muted)', fontSize: 11 }}>—</span>}
                    </td>
                    <td style={{ padding: '10px 12px' }}>
                      {s.ticket_number ? (
                        <span style={{ color: 'var(--t-info)', fontSize: 12 }}>{s.ticket_number}</span>
                      ) : <span style={{ color: 'var(--t-text-muted)', fontSize: 11 }}>—</span>}
                    </td>
                    <td style={{ padding: '10px 12px' }}>
                      {s.el_cost_credits != null ? (
                        <span style={{ fontSize: 12, color: 'var(--t-text-muted)', fontFamily: 'monospace' }}>
                          ${(s.el_cost_credits / 10000).toFixed(3)}
                        </span>
                      ) : <span style={{ color: 'var(--t-text-muted)', fontSize: 11 }}>—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {sessions.some(s => s.el_cost_credits != null) && (
            <div style={{ marginTop: 12, fontSize: 13, color: 'var(--t-text-muted)', textAlign: 'right' }}>
              Page total: <strong style={{ color: 'var(--t-text-bright)' }}>
                ${(sessions.reduce((sum, s) => sum + (s.el_cost_credits ?? 0), 0) / 10000).toFixed(3)}
              </strong>
            </div>
          )}
        </div>
      )}

      {/* ══════════════════════════════════════════════════════
          MESSAGING TAB — SMS & WhatsApp
          ══════════════════════════════════════════════════════ */}
      {activeTab === 'messaging' && (
        <div style={{ display: 'flex', gap: 20, minHeight: 600 }}>

          {/* ── Left Column: Config + Conversations ── */}
          <div style={{ width: 340, flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 16 }}>

            {/* Channel config card */}
            <div style={{
              background: 'var(--t-panel)', borderRadius: 8, padding: 16,
              border: '1px solid var(--t-border)',
            }}>
              <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--t-text-muted)', marginBottom: 12 }}>
                Channels
              </div>

              {/* SMS Toggle */}
              <label style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', marginBottom: 10 }}>
                <input type="checkbox" checked={msgConfigForm.sms_enabled}
                  onChange={e => setMsgConfigForm({ ...msgConfigForm, sms_enabled: e.target.checked })} />
                <span style={{ fontSize: 13, color: 'var(--t-text-bright)', fontWeight: 600 }}>SMS</span>
                <span style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>Text messaging via Twilio</span>
              </label>

              {/* WhatsApp Toggle */}
              <label style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', marginBottom: 10 }}>
                <input type="checkbox" checked={msgConfigForm.whatsapp_enabled}
                  onChange={e => setMsgConfigForm({ ...msgConfigForm, whatsapp_enabled: e.target.checked })} />
                <span style={{ fontSize: 13, color: 'var(--t-text-bright)', fontWeight: 600 }}>WhatsApp</span>
                <span style={{
                  fontSize: 10, padding: '1px 6px', borderRadius: 4, fontWeight: 600,
                  background: msgConfigForm.whatsapp_status === 'approved' ? 'var(--t-accent-bg)' :
                              msgConfigForm.whatsapp_status === 'sandbox' ? 'var(--t-warning-bg)' : 'var(--t-panel-alt)',
                  color: msgConfigForm.whatsapp_status === 'approved' ? 'var(--t-accent)' :
                         msgConfigForm.whatsapp_status === 'sandbox' ? 'var(--t-warning)' : 'var(--t-text-muted)',
                }}>
                  {msgConfigForm.whatsapp_status === 'not_configured' ? 'Not Configured' :
                   msgConfigForm.whatsapp_status === 'sandbox' ? 'Sandbox' :
                   msgConfigForm.whatsapp_status === 'pending' ? 'Pending Approval' : 'Approved'}
                </span>
              </label>

              {/* WhatsApp number (if different from voice) */}
              {msgConfigForm.whatsapp_enabled && (
                <div style={{ marginBottom: 10 }}>
                  <label className="form-label" style={{ fontSize: 11 }}>WhatsApp Number (if different from voice)</label>
                  <input className="form-input" value={msgConfigForm.whatsapp_phone_number}
                    onChange={e => setMsgConfigForm({ ...msgConfigForm, whatsapp_phone_number: e.target.value })}
                    placeholder="Leave blank to use voice number" style={{ fontSize: 12 }} />
                </div>
              )}

              {/* WhatsApp status selector */}
              {msgConfigForm.whatsapp_enabled && (
                <div style={{ marginBottom: 10 }}>
                  <label className="form-label" style={{ fontSize: 11 }}>WhatsApp Status</label>
                  <select className="form-input" value={msgConfigForm.whatsapp_status}
                    onChange={e => setMsgConfigForm({ ...msgConfigForm, whatsapp_status: e.target.value })}
                    style={{ fontSize: 12 }}>
                    <option value="not_configured">Not Configured</option>
                    <option value="sandbox">Sandbox (Testing)</option>
                    <option value="pending">Pending Approval</option>
                    <option value="approved">Approved</option>
                  </select>
                </div>
              )}

              <div style={{ borderTop: '1px solid var(--t-border)', marginTop: 12, paddingTop: 12 }}>
                <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--t-text-muted)', marginBottom: 8 }}>
                  Options
                </div>

                <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', marginBottom: 8, fontSize: 12 }}>
                  <input type="checkbox" checked={msgConfigForm.auto_create_ticket}
                    onChange={e => setMsgConfigForm({ ...msgConfigForm, auto_create_ticket: e.target.checked })} />
                  <span style={{ color: 'var(--t-text-bright)' }}>Auto-create ticket from inbound messages</span>
                </label>

                <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', marginBottom: 8, fontSize: 12 }}>
                  <input type="checkbox" checked={msgConfigForm.auto_reply_enabled}
                    onChange={e => setMsgConfigForm({ ...msgConfigForm, auto_reply_enabled: e.target.checked })} />
                  <span style={{ color: 'var(--t-text-bright)' }}>Auto-reply to inbound messages</span>
                </label>

                {msgConfigForm.auto_reply_enabled && (
                  <textarea className="form-input" rows={2} value={msgConfigForm.auto_reply_message}
                    onChange={e => setMsgConfigForm({ ...msgConfigForm, auto_reply_message: e.target.value })}
                    placeholder="Thanks for reaching out! We'll get back to you shortly."
                    style={{ fontSize: 12, marginBottom: 8 }} />
                )}

                <div style={{ marginBottom: 8 }}>
                  <label className="form-label" style={{ fontSize: 11 }}>Default Language</label>
                  <select className="form-input" value={msgConfigForm.default_language}
                    onChange={e => setMsgConfigForm({ ...msgConfigForm, default_language: e.target.value })}
                    style={{ fontSize: 12 }}>
                    {Object.entries(LANG_LABELS).map(([k, v]) => (
                      <option key={k} value={k}>{v}</option>
                    ))}
                  </select>
                </div>
              </div>

              <button className="btn btn-primary" style={{ width: '100%', marginTop: 8 }}
                onClick={handleSaveMsgConfig} disabled={savingMsgConfig}>
                {savingMsgConfig ? 'Saving...' : 'Save Settings'}
              </button>
            </div>

            {/* Webhook URLs */}
            {(msgConfigForm.sms_enabled || msgConfigForm.whatsapp_enabled) && Object.keys(msgWebhooks).length > 0 && (
              <div style={{
                background: 'var(--t-panel)', borderRadius: 8, padding: 16,
                border: '1px solid var(--t-border)',
              }}>
                <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--t-text-muted)', marginBottom: 12 }}>
                  Twilio Webhook URLs
                </div>
                <p style={{ fontSize: 11, color: 'var(--t-text-muted)', marginBottom: 12, lineHeight: 1.5 }}>
                  Configure these in your Twilio console under your phone number's messaging settings.
                </p>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  {Object.entries(msgWebhooks).map(([key, url]) => (
                    <WebhookRow key={key}
                      label={key === 'inbound_webhook' ? 'Inbound Messages (A message comes in)' : 'Status Callback'}
                      url={url} />
                  ))}
                </div>
              </div>
            )}

            {/* Stats card */}
            {msgStats && (msgStats.active_conversations > 0 || msgStats.inbound_30d > 0) && (
              <div style={{
                background: 'var(--t-panel)', borderRadius: 8, padding: 16,
                border: '1px solid var(--t-border)',
                display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12,
              }}>
                <div>
                  <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--t-text-bright)' }}>{msgStats.active_conversations}</div>
                  <div style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>Active Conversations</div>
                </div>
                <div>
                  <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--t-text-bright)' }}>{msgStats.inbound_30d}</div>
                  <div style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>Inbound (30d)</div>
                </div>
                <div>
                  <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--t-text-bright)' }}>{msgStats.outbound_30d}</div>
                  <div style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>Outbound (30d)</div>
                </div>
                <div>
                  <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--t-text-bright)' }}>
                    ${(msgStats.total_cost_cents_30d / 100).toFixed(2)}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>Cost (30d)</div>
                </div>
              </div>
            )}

            {/* Conversation list */}
            <div style={{
              background: 'var(--t-panel)', borderRadius: 8, padding: 12,
              border: '1px solid var(--t-border)', flex: 1, overflow: 'auto',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                <span style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--t-text-muted)' }}>
                  Conversations
                </span>
                <div style={{ display: 'flex', gap: 4 }}>
                  {(['', 'sms', 'whatsapp'] as const).map(f => (
                    <button key={f || 'all'}
                      className={`btn ${channelFilter === f ? 'btn-primary' : 'btn-ghost'}`}
                      style={{ fontSize: 10, padding: '2px 8px' }}
                      onClick={() => setChannelFilter(f)}>
                      {f === '' ? 'All' : f === 'sms' ? 'SMS' : 'WhatsApp'}
                    </button>
                  ))}
                </div>
              </div>

              {conversations.length === 0 ? (
                <div style={{ color: 'var(--t-text-muted)', fontSize: 12, padding: '24px 0', textAlign: 'center' }}>
                  No conversations yet.
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {conversations.map(conv => (
                    <button key={conv.id}
                      onClick={() => { setSelectedConvId(conv.id); }}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 10,
                        padding: '10px 12px', borderRadius: 6,
                        cursor: 'pointer', width: '100%', textAlign: 'left',
                        background: selectedConvId === conv.id ? 'var(--t-accent-bg)' : 'var(--t-panel-alt)',
                        border: selectedConvId === conv.id ? '1px solid var(--t-accent-border)' : '1px solid transparent',
                      }}>
                      <span style={{
                        fontSize: 10, fontWeight: 700, padding: '2px 6px', borderRadius: 4,
                        background: conv.channel === 'whatsapp' ? '#25D36622' : 'var(--t-info-bg)',
                        color: conv.channel === 'whatsapp' ? '#25D366' : 'var(--t-info)',
                      }}>
                        {conv.channel === 'whatsapp' ? 'WA' : 'SMS'}
                      </span>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 13, color: 'var(--t-text-bright)', fontWeight: 500 }}>
                          {conv.contact_name || conv.contact_phone}
                        </div>
                        <div style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>
                          {conv.message_count} msg{conv.message_count !== 1 ? 's' : ''}
                          {conv.last_message_at && ` · ${new Date(conv.last_message_at).toLocaleDateString()}`}
                        </div>
                      </div>
                      {conv.ticket_id && (
                        <span style={{ fontSize: 10, color: 'var(--t-info)', fontWeight: 600 }}>TKT</span>
                      )}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* ── Right Column: Message Thread + Templates ── */}
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 16 }}>

            {/* Message thread */}
            {selectedConvId ? (
              <div style={{
                background: 'var(--t-panel)', borderRadius: 8,
                border: '1px solid var(--t-border)',
                display: 'flex', flexDirection: 'column', flex: 1,
              }}>
                {/* Thread header */}
                {(() => {
                  const conv = conversations.find(c => c.id === selectedConvId);
                  if (!conv) return null;
                  const isWhatsApp = conv.channel === 'whatsapp';
                  const sessionExpired = isWhatsApp && conv.last_inbound_at &&
                    (Date.now() - new Date(conv.last_inbound_at).getTime()) > 86400000;
                  return (
                    <div style={{
                      padding: '12px 16px', borderBottom: '1px solid var(--t-border)',
                      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    }}>
                      <div>
                        <span style={{
                          fontSize: 10, fontWeight: 700, padding: '2px 6px', borderRadius: 4, marginRight: 8,
                          background: isWhatsApp ? '#25D36622' : 'var(--t-info-bg)',
                          color: isWhatsApp ? '#25D366' : 'var(--t-info)',
                        }}>
                          {isWhatsApp ? 'WhatsApp' : 'SMS'}
                        </span>
                        <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--t-text-bright)' }}>
                          {conv.contact_name || conv.contact_phone}
                        </span>
                        {conv.contact_name && (
                          <span style={{ fontSize: 12, color: 'var(--t-text-muted)', marginLeft: 8 }}>
                            {conv.contact_phone}
                          </span>
                        )}
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        {isWhatsApp && sessionExpired && (
                          <span style={{ fontSize: 10, color: 'var(--t-warning)', fontWeight: 600 }}>
                            24h session expired — use template
                          </span>
                        )}
                        <span style={{ fontSize: 11, color: 'var(--t-text-muted)' }}>
                          {LANG_FLAGS[conv.language] || conv.language}
                        </span>
                      </div>
                    </div>
                  );
                })()}

                {/* Messages */}
                <div style={{ flex: 1, overflow: 'auto', padding: 16, display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {messages.length === 0 ? (
                    <div style={{ color: 'var(--t-text-muted)', fontSize: 12, textAlign: 'center', padding: '40px 0' }}>
                      No messages yet.
                    </div>
                  ) : messages.map(msg => (
                    <div key={msg.id} style={{
                      display: 'flex',
                      justifyContent: msg.direction === 'outbound' ? 'flex-end' : 'flex-start',
                    }}>
                      <div style={{
                        maxWidth: '70%', padding: '10px 14px', borderRadius: 12,
                        background: msg.direction === 'outbound' ? 'var(--t-accent-bg)' : 'var(--t-panel-alt)',
                        border: `1px solid ${msg.direction === 'outbound' ? 'var(--t-accent-border)' : 'var(--t-border)'}`,
                      }}>
                        <div style={{ fontSize: 13, color: 'var(--t-text-bright)', lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>
                          {msg.body || '(no content)'}
                        </div>
                        <div style={{ fontSize: 10, color: 'var(--t-text-muted)', marginTop: 4, display: 'flex', gap: 8, alignItems: 'center' }}>
                          <span>{new Date(msg.created_at).toLocaleTimeString()}</span>
                          {msg.direction === 'outbound' && (
                            <span style={{
                              color: msg.status === 'delivered' ? 'var(--t-accent)' :
                                     msg.status === 'read' ? 'var(--t-info)' :
                                     msg.status === 'failed' ? 'var(--t-error)' : 'var(--t-text-muted)',
                            }}>
                              {msg.status === 'delivered' ? 'Delivered' :
                               msg.status === 'read' ? 'Read' :
                               msg.status === 'sent' ? 'Sent' :
                               msg.status === 'failed' ? 'Failed' :
                               msg.status === 'queued' ? 'Sending...' : msg.status}
                            </span>
                          )}
                          {msg.cost_cents != null && (
                            <span style={{ fontFamily: 'monospace' }}>${(msg.cost_cents / 100).toFixed(4)}</span>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>

                {/* Reply input */}
                <div style={{
                  padding: '12px 16px', borderTop: '1px solid var(--t-border)',
                  display: 'flex', gap: 8,
                }}>
                  <textarea className="form-input" rows={2} value={msgReply}
                    onChange={e => setMsgReply(e.target.value)}
                    onKeyDown={e => { if (e.ctrlKey && e.key === 'Enter') handleSendMessage(); }}
                    placeholder="Type a message... (Ctrl+Enter to send)"
                    style={{ flex: 1, fontSize: 13, resize: 'none' }} />
                  <button className="btn btn-primary" onClick={handleSendMessage}
                    disabled={sendingMsg || !msgReply.trim()}
                    style={{ alignSelf: 'flex-end' }}>
                    {sendingMsg ? 'Sending...' : 'Send'}
                  </button>
                </div>
              </div>
            ) : (
              <div style={{
                background: 'var(--t-panel)', borderRadius: 8, flex: 1,
                border: '1px solid var(--t-border)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                color: 'var(--t-text-muted)', fontSize: 14,
              }}>
                {conversations.length > 0
                  ? 'Select a conversation to view messages'
                  : 'No conversations yet. Inbound SMS/WhatsApp messages will appear here.'}
              </div>
            )}

            {/* Templates section */}
            {msgConfigForm.whatsapp_enabled && (
              <div style={{
                background: 'var(--t-panel)', borderRadius: 8, padding: 16,
                border: '1px solid var(--t-border)',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                  <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--t-text-muted)' }}>
                    WhatsApp Templates
                  </div>
                  <button className="btn btn-ghost" style={{ fontSize: 11 }}
                    onClick={() => setShowTemplateForm(!showTemplateForm)}>
                    {showTemplateForm ? 'Cancel' : '+ New Template'}
                  </button>
                </div>

                <p style={{ fontSize: 11, color: 'var(--t-text-muted)', marginBottom: 12, lineHeight: 1.5 }}>
                  WhatsApp requires pre-approved templates for messages sent outside the 24-hour session window.
                  Create templates here, then submit them for approval in your Twilio console.
                </p>

                {showTemplateForm && (
                  <div style={{
                    background: 'var(--t-panel-alt)', borderRadius: 6, padding: 12, marginBottom: 12,
                    display: 'flex', flexDirection: 'column', gap: 8,
                  }}>
                    <input className="form-input" value={templateForm.name}
                      onChange={e => setTemplateForm({ ...templateForm, name: e.target.value })}
                      placeholder="Template name (e.g., welcome_message)" style={{ fontSize: 12 }} />
                    <div style={{ display: 'flex', gap: 8 }}>
                      <select className="form-input" value={templateForm.language}
                        onChange={e => setTemplateForm({ ...templateForm, language: e.target.value })}
                        style={{ fontSize: 12, flex: 1 }}>
                        {Object.entries(LANG_LABELS).map(([k, v]) => (
                          <option key={k} value={k}>{v}</option>
                        ))}
                      </select>
                      <select className="form-input" value={templateForm.category}
                        onChange={e => setTemplateForm({ ...templateForm, category: e.target.value })}
                        style={{ fontSize: 12, flex: 1 }}>
                        <option value="utility">Utility</option>
                        <option value="marketing">Marketing</option>
                        <option value="authentication">Authentication</option>
                      </select>
                    </div>
                    <textarea className="form-input" rows={3} value={templateForm.body}
                      onChange={e => setTemplateForm({ ...templateForm, body: e.target.value })}
                      placeholder="Hello {{1}}, your ticket {{2}} has been updated."
                      style={{ fontSize: 12 }} />
                    <button className="btn btn-primary" style={{ alignSelf: 'flex-start', fontSize: 12 }}
                      onClick={handleCreateTemplate} disabled={!templateForm.name || !templateForm.body}>
                      Create Template
                    </button>
                  </div>
                )}

                {templates.length > 0 ? (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {templates.map(tmpl => (
                      <div key={tmpl.id} style={{
                        display: 'flex', alignItems: 'center', gap: 10,
                        padding: '8px 12px', borderRadius: 6,
                        background: 'var(--t-panel-alt)',
                      }}>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontSize: 13, color: 'var(--t-text-bright)', fontWeight: 500 }}>
                            {tmpl.name}
                            <span style={{ fontSize: 10, color: 'var(--t-text-muted)', marginLeft: 6 }}>
                              {LANG_FLAGS[tmpl.language] || tmpl.language}
                            </span>
                          </div>
                          <div style={{ fontSize: 11, color: 'var(--t-text-muted)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {tmpl.body}
                          </div>
                        </div>
                        <span style={{
                          fontSize: 10, fontWeight: 600, padding: '2px 6px', borderRadius: 4,
                          background: tmpl.status === 'approved' ? 'var(--t-accent-bg)' :
                                      tmpl.status === 'rejected' ? 'var(--t-error-bg)' :
                                      tmpl.status === 'pending' ? 'var(--t-warning-bg)' : 'var(--t-panel)',
                          color: tmpl.status === 'approved' ? 'var(--t-accent)' :
                                 tmpl.status === 'rejected' ? 'var(--t-error)' :
                                 tmpl.status === 'pending' ? 'var(--t-warning)' : 'var(--t-text-muted)',
                        }}>
                          {tmpl.status}
                        </span>
                        <button className="btn btn-ghost" style={{ fontSize: 10, color: 'var(--t-error)', padding: '2px 6px' }}
                          onClick={() => handleDeleteTemplate(tmpl.id)}>
                          Delete
                        </button>
                      </div>
                    ))}
                  </div>
                ) : !showTemplateForm && (
                  <div style={{ color: 'var(--t-text-muted)', fontSize: 12, textAlign: 'center', padding: '12px 0' }}>
                    No templates yet.
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
