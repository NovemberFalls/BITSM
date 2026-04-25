import { useEffect, useState } from 'react';
import { useAutomationStore } from '../../store/automationStore';
import { api } from '../../api/client';
import type { Node } from '@xyflow/react';
import type { Agent } from '../../types';

const TRIGGER_SUBTYPES = [
  { value: 'ticket_created', label: 'Ticket Created' },
  { value: 'status_changed', label: 'Status Changed' },
  { value: 'priority_changed', label: 'Priority Changed' },
  { value: 'comment_added', label: 'Comment Added' },
  { value: 'assignee_changed', label: 'Assignee Changed' },
  { value: 'tag_added', label: 'Tag Added' },
  { value: 'sla_breached', label: 'SLA Breached' },
  { value: 'schedule', label: 'Schedule (Cron)' },
];

const CONDITION_SUBTYPES = [
  { value: 'assignee_set', label: 'Assignee Set?' },
  { value: 'category_is', label: 'Category Is' },
  { value: 'custom_field_equals', label: 'Custom Field Value' },
  { value: 'hours_since', label: 'Hours Since' },
  { value: 'location_is', label: 'Location Is' },
  { value: 'priority_is', label: 'Priority Is' },
  { value: 'requester_role', label: 'Requester Role' },
  { value: 'status_is', label: 'Status Is' },
  { value: 'tag_contains', label: 'Tag Contains' },
];

const ACTION_SUBTYPES = [
  { value: 'add_tag', label: 'Add Tag' },
  { value: 'assign_team', label: 'Assign Team' },
  { value: 'assign_to', label: 'Assign To Agent' },
  { value: 'change_priority', label: 'Change Priority' },
  { value: 'change_status', label: 'Change Status' },
  { value: 'do_nothing', label: 'Do Nothing' },
  { value: 'email_group', label: 'Email Group' },
  { value: 'post_comment', label: 'Post Comment' },
  { value: 'remove_tag', label: 'Remove Tag' },
  { value: 'send_notification', label: 'Send Notification' },
  { value: 'set_custom_field', label: 'Set Custom Field' },
  { value: 'webhook', label: 'Webhook' },
];

export function NodeConfigPanel() {
  const { nodes, selectedNodeId, updateNodeConfig, updateNodeLabel, selectNode, setNodes, setEdges, edges } = useAutomationStore();
  const node = nodes.find((n) => n.id === selectedNodeId);

  if (!node) {
    return (
      <div className="auto-config-panel">
        <div className="auto-config-empty">Select a node to configure</div>
      </div>
    );
  }

  const nodeType = node.type as string;
  const subtype = node.data?.subtype as string;
  const config = (node.data?.config || {}) as Record<string, any>;
  const label = (node.data?.label || '') as string;

  const setConfig = (key: string, value: any) => {
    updateNodeConfig(node.id, { ...config, [key]: value });
  };

  const setSubtype = (newSubtype: string) => {
    // Update node data with new subtype + clear config
    const updatedNodes = nodes.map((n) => {
      if (n.id !== node.id) return n;
      return {
        ...n,
        data: {
          ...n.data,
          subtype: newSubtype,
          config: {},
          label: '',
        },
      };
    });
    useAutomationStore.getState().setNodes(updatedNodes);
  };

  const removeNode = () => {
    const newNodes = nodes.filter((n) => n.id !== node.id);
    const newEdges = edges.filter((e) => e.source !== node.id && e.target !== node.id);
    useAutomationStore.getState().setNodes(newNodes);
    useAutomationStore.getState().setEdges(newEdges);
    selectNode(null);
  };

  return (
    <div className="auto-config-panel">
      <div className="auto-config-header">
        <span className={`auto-config-badge auto-config-badge-${nodeType}`}>
          {nodeType.charAt(0).toUpperCase() + nodeType.slice(1)}
        </span>
        <button className="btn btn-ghost btn-xs" onClick={() => selectNode(null)}>Close</button>
      </div>

      <div className="auto-config-field">
        <label>Label</label>
        <input
          type="text"
          className="input"
          value={label}
          onChange={(e) => updateNodeLabel(node.id, e.target.value)}
          placeholder="Optional display label"
        />
      </div>

      {nodeType === 'trigger' && (
        <div className="auto-config-field">
          <label>Trigger Type</label>
          <select className="input" value={subtype} onChange={(e) => setSubtype(e.target.value)}>
            {TRIGGER_SUBTYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
        </div>
      )}

      {/* Condition type selector is now inside ConditionGroupConfig per-row */}

      {nodeType === 'action' && (
        <div className="auto-config-field">
          <label>Action Type</label>
          <select className="input" value={subtype} onChange={(e) => setSubtype(e.target.value)}>
            {ACTION_SUBTYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
        </div>
      )}

      {nodeType === 'comment' && (
        <div className="auto-config-field">
          <label>Note</label>
          <textarea
            className="input"
            rows={4}
            value={label}
            onChange={(e) => updateNodeLabel(node.id, e.target.value)}
            placeholder="Write a note about this part of the workflow..."
          />
        </div>
      )}

      {/* Subtype-specific config fields */}
      {nodeType === 'condition' && (
        <div className="auto-config-section">
          <ConditionGroupConfig node={node} updateNodeConfig={updateNodeConfig} />
        </div>
      )}
      {nodeType !== 'condition' && nodeType !== 'comment' && (
        <div className="auto-config-section">
          <SubtypeConfig
            subtype={subtype}
            config={config}
            setConfig={setConfig}
            mergeConfig={(updates) => updateNodeConfig(node.id, { ...config, ...updates })}
          />
        </div>
      )}

      <div className="auto-config-actions">
        <button className="btn btn-danger btn-sm" onClick={removeNode}>Remove Node</button>
      </div>
    </div>
  );
}


function SubtypeConfig({ subtype, config, setConfig, mergeConfig }: {
  subtype: string;
  config: Record<string, any>;
  setConfig: (key: string, value: any) => void;
  mergeConfig: (updates: Record<string, any>) => void;
}) {
  switch (subtype) {
    // Trigger configs
    case 'status_changed':
      return (
        <>
          <ConfigField label="From Status">
            <select className="input" value={config.from || ''} onChange={(e) => setConfig('from', e.target.value || undefined)}>
              <option value="">Any</option>
              <option value="open">Open</option>
              <option value="pending">Pending</option>
              <option value="resolved">Resolved</option>
              <option value="closed_not_resolved">Closed (Not Resolved)</option>
            </select>
          </ConfigField>
          <ConfigField label="To Status">
            <select className="input" value={config.to || ''} onChange={(e) => setConfig('to', e.target.value || undefined)}>
              <option value="">Any</option>
              <option value="open">Open</option>
              <option value="pending">Pending</option>
              <option value="resolved">Resolved</option>
              <option value="closed_not_resolved">Closed (Not Resolved)</option>
            </select>
          </ConfigField>
        </>
      );

    case 'priority_changed':
      return (
        <>
          <ConfigField label="From Priority">
            <select className="input" value={config.from || ''} onChange={(e) => setConfig('from', e.target.value || undefined)}>
              <option value="">Any</option>
              {['p1', 'p2', 'p3', 'p4'].map((p) => <option key={p} value={p}>{p.toUpperCase()}</option>)}
            </select>
          </ConfigField>
          <ConfigField label="To Priority">
            <select className="input" value={config.to || ''} onChange={(e) => setConfig('to', e.target.value || undefined)}>
              <option value="">Any</option>
              {['p1', 'p2', 'p3', 'p4'].map((p) => <option key={p} value={p}>{p.toUpperCase()}</option>)}
            </select>
          </ConfigField>
        </>
      );

    case 'comment_added':
      return (
        <ConfigField label="Comment Type">
          <select className="input" value={config.comment_type || ''} onChange={(e) => setConfig('comment_type', e.target.value || undefined)}>
            <option value="">Any</option>
            <option value="public">Public</option>
            <option value="internal">Internal</option>
          </select>
        </ConfigField>
      );

    case 'schedule':
      return (
        <ConfigField label="Cron Expression">
          <input className="input" type="text" value={config.cron || ''} onChange={(e) => setConfig('cron', e.target.value)} placeholder="0 9 * * 1-5" />
          <span className="auto-config-hint">Standard 5-field cron (minute hour dom month dow)</span>
        </ConfigField>
      );

    case 'tag_added':
      return (
        <ConfigField label="Tag (optional filter)">
          <input className="input" type="text" value={config.tag || ''} onChange={(e) => setConfig('tag', e.target.value)} placeholder="Leave empty for any tag" />
        </ConfigField>
      );

    // Action configs
    case 'assign_to':
      return <AgentPicker config={config} mergeConfig={mergeConfig} />;

    case 'do_nothing':
      return <div className="auto-config-hint">This node does nothing. Use it to cap a condition branch that needs no action.</div>;

    case 'change_priority':
      return (
        <ConfigField label="New Priority">
          <select className="input" value={config.priority || ''} onChange={(e) => setConfig('priority', e.target.value)}>
            <option value="">Select...</option>
            {['p1', 'p2', 'p3', 'p4'].map((p) => <option key={p} value={p}>{p.toUpperCase()}</option>)}
          </select>
        </ConfigField>
      );

    case 'change_status':
      return (
        <ConfigField label="New Status">
          <select className="input" value={config.status || ''} onChange={(e) => setConfig('status', e.target.value)}>
            <option value="">Select...</option>
            <option value="open">Open</option>
            <option value="pending">Pending</option>
            <option value="resolved">Resolved</option>
            <option value="closed_not_resolved">Closed (Not Resolved)</option>
          </select>
        </ConfigField>
      );

    case 'add_tag':
    case 'remove_tag':
      return (
        <ConfigField label="Tag">
          <input className="input" type="text" value={config.tag || ''} onChange={(e) => setConfig('tag', e.target.value)} placeholder="e.g. escalated" />
        </ConfigField>
      );

    case 'post_comment':
      return (
        <>
          <ConfigField label="Comment Content">
            <textarea className="input" rows={3} value={config.content || ''} onChange={(e) => setConfig('content', e.target.value)} placeholder="Comment text..." />
          </ConfigField>
          <ConfigField label="Visibility">
            <select className="input" value={config.is_internal === false ? 'false' : 'true'} onChange={(e) => setConfig('is_internal', e.target.value === 'true')}>
              <option value="true">Internal Note</option>
              <option value="false">Public Reply</option>
            </select>
          </ConfigField>
        </>
      );

    case 'send_notification':
      return (
        <>
          <ConfigField label="Channel">
            <select className="input" value={config.channel || 'teams'} onChange={(e) => setConfig('channel', e.target.value)}>
              <option value="teams">Microsoft Teams</option>
              <option value="email">Email</option>
            </select>
          </ConfigField>
          <ConfigField label="Message">
            <textarea className="input" rows={3} value={config.message || ''} onChange={(e) => setConfig('message', e.target.value)} placeholder="Notification message..." />
          </ConfigField>
        </>
      );

    case 'webhook':
      return <WebhookConfig config={config} setConfig={setConfig} />;

    case 'assign_team':
      return <TeamPicker config={config} mergeConfig={mergeConfig} />;

    case 'set_custom_field':
      return <CustomFieldSetter config={config} mergeConfig={mergeConfig} />;

    case 'email_group':
      return <EmailGroupConfig config={config} setConfig={setConfig} />;

    default:
      return <div className="auto-config-hint">No configuration needed for this type.</div>;
  }
}


// ============================================================
// Multi-condition group (AND / OR)
// ============================================================

type ConditionRow = { subtype: string; config: Record<string, any> };

function ConditionGroupConfig({ node, updateNodeConfig }: {
  node: Node;
  updateNodeConfig: (id: string, config: Record<string, any>) => void;
}) {
  const rawConfig = (node.data.config || {}) as Record<string, any>;
  const legacySubtype = (node.data.subtype || 'priority_is') as string;

  // Normalize to multi-condition format (migrate old single-condition nodes on open)
  let conditions: ConditionRow[];
  let logic: 'and' | 'or';

  if (Array.isArray(rawConfig.conditions) && rawConfig.conditions.length > 0) {
    conditions = rawConfig.conditions;
    logic = rawConfig.logic === 'or' ? 'or' : 'and';
  } else {
    // Old format — promote to conditions array
    const { logic: _l, conditions: _c, ...singleConfig } = rawConfig;
    conditions = [{ subtype: legacySubtype, config: singleConfig }];
    logic = 'and';
  }

  const save = (newLogic: 'and' | 'or', newConditions: ConditionRow[]) => {
    updateNodeConfig(node.id, { logic: newLogic, conditions: newConditions });
  };

  const addCondition = () => save(logic, [...conditions, { subtype: 'priority_is', config: {} }]);
  const removeCondition = (i: number) => save(logic, conditions.filter((_, idx) => idx !== i));
  const updateSubtype = (i: number, s: string) =>
    save(logic, conditions.map((c, idx) => idx === i ? { subtype: s, config: {} } : c));
  const updateConfig = (i: number, cfg: Record<string, any>) =>
    save(logic, conditions.map((c, idx) => idx === i ? { ...c, config: cfg } : c));

  return (
    <div className="auto-condition-group">
      {conditions.map((cond, i) => (
        <div key={i}>
          {i > 0 && (
            <div className="auto-logic-row">
              <button
                className={`auto-logic-btn${logic === 'and' ? ' active' : ''}`}
                onClick={() => save('and', conditions)}
              >AND</button>
              <button
                className={`auto-logic-btn${logic === 'or' ? ' active' : ''}`}
                onClick={() => save('or', conditions)}
              >OR</button>
            </div>
          )}
          <div className="auto-condition-row">
            <div className="auto-config-field">
              <label>Condition Type</label>
              <select className="input" value={cond.subtype} onChange={(e) => updateSubtype(i, e.target.value)}>
                {CONDITION_SUBTYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
            </div>
            <SingleConditionConfig
              subtype={cond.subtype}
              config={cond.config}
              setConfig={(key, val) => updateConfig(i, { ...cond.config, [key]: val })}
            />
            {conditions.length > 1 && (
              <button className="btn btn-ghost btn-xs" style={{ marginTop: '4px', color: 'var(--t-danger)' }} onClick={() => removeCondition(i)}>
                Remove condition
              </button>
            )}
          </div>
        </div>
      ))}
      <button className="btn btn-ghost btn-sm" style={{ marginTop: '8px', width: '100%' }} onClick={addCondition}>
        + Add Condition
      </button>
    </div>
  );
}


function SingleConditionConfig({ subtype, config, setConfig }: {
  subtype: string;
  config: Record<string, any>;
  setConfig: (key: string, value: any) => void;
}) {
  switch (subtype) {
    case 'priority_is':
      return (
        <ConfigField label="Priorities">
          <MultiCheckbox
            options={[
              { value: 'p1', label: 'P1 - Urgent' },
              { value: 'p2', label: 'P2 - High' },
              { value: 'p3', label: 'P3 - Medium' },
              { value: 'p4', label: 'P4 - Low' },
            ]}
            selected={config.values || []}
            onChange={(vals) => setConfig('values', vals)}
          />
        </ConfigField>
      );
    case 'status_is':
      return (
        <ConfigField label="Statuses">
          <MultiCheckbox
            options={[
              { value: 'open', label: 'Open' },
              { value: 'pending', label: 'Pending' },
              { value: 'resolved', label: 'Resolved' },
              { value: 'closed_not_resolved', label: 'Closed' },
            ]}
            selected={config.values || []}
            onChange={(vals) => setConfig('values', vals)}
          />
        </ConfigField>
      );
    case 'tag_contains':
      return (
        <ConfigField label="Tags (comma-separated)">
          <input
            className="input"
            type="text"
            value={(config.tags || []).join(', ')}
            onChange={(e) => setConfig('tags', e.target.value.split(',').map((s: string) => s.trim()).filter(Boolean))}
            placeholder="billing, urgent"
          />
        </ConfigField>
      );
    case 'assignee_set':
      return (
        <ConfigField label="Condition">
          <select className="input" value={config.is_set === false ? 'false' : 'true'} onChange={(e) => setConfig('is_set', e.target.value === 'true')}>
            <option value="true">Has assignee</option>
            <option value="false">No assignee</option>
          </select>
        </ConfigField>
      );
    case 'requester_role':
      return (
        <ConfigField label="Roles">
          <MultiCheckbox
            options={[
              { value: 'end_user', label: 'End User' },
              { value: 'agent', label: 'Agent' },
              { value: 'tenant_admin', label: 'Tenant Admin' },
            ]}
            selected={config.roles || []}
            onChange={(vals) => setConfig('roles', vals)}
          />
        </ConfigField>
      );
    case 'custom_field_equals':
      return <CustomFieldConditionConfig config={config} setConfig={setConfig} />;
    case 'hours_since':
      return (
        <>
          <ConfigField label="Field">
            <select className="input" value={config.field || 'created_at'} onChange={(e) => setConfig('field', e.target.value)}>
              <option value="created_at">Created At</option>
              <option value="updated_at">Updated At</option>
              <option value="sla_due_at">SLA Due At</option>
            </select>
          </ConfigField>
          <ConfigField label="Operator">
            <select className="input" value={config.operator || '>'} onChange={(e) => setConfig('operator', e.target.value)}>
              <option value=">">&gt; (greater than)</option>
              <option value="<">&lt; (less than)</option>
              <option value=">=">&gt;= (greater or equal)</option>
              <option value="<=">&lt;= (less or equal)</option>
            </select>
          </ConfigField>
          <ConfigField label="Hours">
            <input className="input" type="number" min={0} value={config.value || 0} onChange={(e) => setConfig('value', Number(e.target.value))} />
          </ConfigField>
        </>
      );
    default:
      return null;
  }
}


function ConfigField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="auto-config-field">
      <label>{label}</label>
      {children}
    </div>
  );
}


function AgentPicker({ config, mergeConfig }: { config: Record<string, any>; mergeConfig: (updates: Record<string, any>) => void }) {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.listAgents().then((data) => {
      setAgents(data);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  return (
    <ConfigField label="Assign To Agent">
      {loading ? (
        <div className="auto-config-hint">Loading agents...</div>
      ) : (
        <select
          className="input"
          value={config.user_id || ''}
          onChange={(e) => {
            const id = Number(e.target.value) || undefined;
            const agent = agents.find((a) => a.id === id);
            mergeConfig({ user_id: id, agent_name: agent?.name || '' });
          }}
        >
          <option value="">Select agent...</option>
          {agents.map((a) => (
            <option key={a.id} value={a.id}>{a.name} ({a.email})</option>
          ))}
        </select>
      )}
    </ConfigField>
  );
}


function TeamPicker({ config, mergeConfig }: { config: Record<string, any>; mergeConfig: (updates: Record<string, any>) => void }) {
  const [teams, setTeams] = useState<{ id: number; name: string }[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.listTeams().then((data) => {
      setTeams(data);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  return (
    <ConfigField label="Assign To Team">
      {loading ? (
        <div className="auto-config-hint">Loading teams...</div>
      ) : (
        <select
          className="input"
          value={config.team_id || ''}
          onChange={(e) => {
            const id = Number(e.target.value) || undefined;
            const team = teams.find((t) => t.id === id);
            mergeConfig({ team_id: id, team_name: team?.name || '' });
          }}
        >
          <option value="">Select team...</option>
          {teams.map((t) => (
            <option key={t.id} value={t.id}>{t.name}</option>
          ))}
        </select>
      )}
    </ConfigField>
  );
}


function CustomFieldSetter({ config, mergeConfig }: { config: Record<string, any>; mergeConfig: (updates: Record<string, any>) => void }) {
  const [fields, setFields] = useState<{ field_key: string; name: string; field_type: string; options: { label: string; value: string }[] }[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.listCustomFields().then((data) => {
      setFields((data.fields || []).filter((f: any) => f.is_active !== false));
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  const selectedField = fields.find((f) => f.field_key === config.field_key);
  const opts: { label: string; value: string }[] = selectedField?.options || [];

  return (
    <>
      <ConfigField label="Custom Field">
        {loading ? (
          <div className="auto-config-hint">Loading fields...</div>
        ) : (
          <select
            className="input"
            value={config.field_key || ''}
            onChange={(e) => mergeConfig({ field_key: e.target.value, value: '' })}
          >
            <option value="">Select field...</option>
            {fields.map((f) => (
              <option key={f.field_key} value={f.field_key}>{f.name}</option>
            ))}
          </select>
        )}
      </ConfigField>
      {config.field_key && (
        <ConfigField label="Value to Set">
          {selectedField?.field_type === 'checkbox' ? (
            <select className="input" value={String(config.value ?? 'true')} onChange={(e) => mergeConfig({ ...config, value: e.target.value === 'true' })}>
              <option value="true">Checked (true)</option>
              <option value="false">Unchecked (false)</option>
            </select>
          ) : (selectedField?.field_type === 'select' || selectedField?.field_type === 'multi_select') ? (
            <select className="input" value={config.value ?? ''} onChange={(e) => mergeConfig({ ...config, value: e.target.value })}>
              <option value="">Select value...</option>
              {opts.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          ) : (
            <input
              className="input"
              type="text"
              value={config.value ?? ''}
              onChange={(e) => mergeConfig({ ...config, value: e.target.value })}
              placeholder="Value to set on the ticket..."
            />
          )}
        </ConfigField>
      )}
    </>
  );
}


function CustomFieldConditionConfig({ config, setConfig }: { config: Record<string, any>; setConfig: (key: string, value: any) => void }) {
  const [fields, setFields] = useState<{ field_key: string; name: string; field_type: string; options: { label: string; value: string }[] }[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.listCustomFields().then((data) => {
      setFields((data.fields || []).filter((f: any) => f.is_active !== false));
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  const selectedField = fields.find((f) => f.field_key === config.field_key);
  const opts: { label: string; value: string }[] = selectedField?.options || [];
  const needsValue = !['set', 'unset'].includes(config.operator || 'eq');
  const isChoice = selectedField?.field_type === 'select' || selectedField?.field_type === 'multi_select';
  const isCheckbox = selectedField?.field_type === 'checkbox';

  return (
    <>
      <ConfigField label="Custom Field">
        {loading ? (
          <div className="auto-config-hint">Loading fields...</div>
        ) : (
          <select className="input" value={config.field_key || ''} onChange={(e) => setConfig('field_key', e.target.value)}>
            <option value="">Select field...</option>
            {fields.map((f) => (
              <option key={f.field_key} value={f.field_key}>{f.name}</option>
            ))}
          </select>
        )}
      </ConfigField>
      <ConfigField label="Operator">
        <select className="input" value={config.operator || 'eq'} onChange={(e) => setConfig('operator', e.target.value)}>
          <option value="eq">Equals</option>
          <option value="neq">Not Equals</option>
          <option value="contains">Contains</option>
          <option value="set">Is Set</option>
          <option value="unset">Is Not Set</option>
        </select>
      </ConfigField>
      {needsValue && (
        <ConfigField label="Value">
          {isCheckbox ? (
            <select className="input" value={String(config.value ?? 'true')} onChange={(e) => setConfig('value', e.target.value)}>
              <option value="true">Checked (true)</option>
              <option value="false">Unchecked (false)</option>
            </select>
          ) : isChoice ? (
            <select className="input" value={config.value || ''} onChange={(e) => setConfig('value', e.target.value)}>
              <option value="">Select value...</option>
              {opts.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          ) : (
            <input className="input" type="text" value={config.value || ''} onChange={(e) => setConfig('value', e.target.value)} placeholder="Expected value..." />
          )}
        </ConfigField>
      )}
    </>
  );
}


function EmailGroupConfig({ config, setConfig }: { config: Record<string, any>; setConfig: (key: string, value: any) => void }) {
  const [groups, setGroups] = useState<{ id: number; name: string }[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch('/api/notifications/groups', { credentials: 'include' })
      .then((r) => r.json())
      .then((data) => {
        setGroups(data);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  return (
    <>
      <ConfigField label="Notification Group">
        {loading ? (
          <div className="auto-config-hint">Loading groups...</div>
        ) : (
          <select
            className="input"
            value={config.notification_group_id || ''}
            onChange={(e) => {
              const id = Number(e.target.value) || undefined;
              const group = groups.find((g) => g.id === id);
              setConfig('notification_group_id', id);
              setConfig('group_name', group?.name || '');
            }}
          >
            <option value="">Select group...</option>
            {groups.map((g) => (
              <option key={g.id} value={g.id}>{g.name}</option>
            ))}
          </select>
        )}
      </ConfigField>
      <ConfigField label="Subject">
        <input
          className="input"
          type="text"
          value={config.subject || ''}
          onChange={(e) => setConfig('subject', e.target.value)}
          placeholder="Email subject..."
        />
      </ConfigField>
      <ConfigField label="Body">
        <textarea
          className="input"
          rows={4}
          value={config.body || ''}
          onChange={(e) => setConfig('body', e.target.value)}
          placeholder="Email body (HTML supported)..."
        />
      </ConfigField>
    </>
  );
}


function WebhookConfig({ config, setConfig }: { config: Record<string, any>; setConfig: (key: string, value: any) => void }) {
  const [showPayload, setShowPayload] = useState(false);

  return (
    <>
      <ConfigField label="URL">
        <input className="input" type="url" value={config.url || ''} onChange={(e) => setConfig('url', e.target.value)} placeholder="https://..." />
      </ConfigField>
      <ConfigField label="Method">
        <select className="input" value={config.method || 'POST'} onChange={(e) => setConfig('method', e.target.value)}>
          <option value="POST">POST</option>
          <option value="GET">GET</option>
          <option value="PUT">PUT</option>
        </select>
      </ConfigField>
      <div className="auto-config-field">
        <button
          className="btn btn-ghost btn-xs auto-config-payload-toggle"
          onClick={() => setShowPayload((p) => !p)}
        >
          ℹ {showPayload ? 'Hide payload schema' : 'View payload schema'}
        </button>
        {showPayload && (
          <pre className="auto-config-payload-preview">{`{
  "ticket_id": 123,
  "tenant_id": 1,
  "ticket_number": "TKT-00001",
  "subject": "Printer not working",
  "status": "open",
  "priority": "p2",
  "custom_fields": {
    "field_key": "value"
  }
}`}</pre>
        )}
      </div>
    </>
  );
}


function MultiCheckbox({ options, selected, onChange }: {
  options: { value: string; label: string }[];
  selected: string[];
  onChange: (values: string[]) => void;
}) {
  const toggle = (value: string) => {
    if (selected.includes(value)) {
      onChange(selected.filter((v) => v !== value));
    } else {
      onChange([...selected, value]);
    }
  };

  return (
    <div className="auto-config-checkboxes">
      {options.map((o) => (
        <label key={o.value} className="auto-config-checkbox">
          <input type="checkbox" checked={selected.includes(o.value)} onChange={() => toggle(o.value)} />
          <span>{o.label}</span>
        </label>
      ))}
    </div>
  );
}
