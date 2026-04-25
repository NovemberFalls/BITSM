import { Handle, Position, type NodeProps } from '@xyflow/react';

const CONDITION_LABELS: Record<string, string> = {
  priority_is: 'Priority Is',
  status_is: 'Status Is',
  category_is: 'Category Is',
  location_is: 'Location Is',
  tag_contains: 'Tag Contains',
  assignee_set: 'Assignee Set?',
  requester_role: 'Requester Role',
  hours_since: 'Hours Since',
};

/** Build a human-readable description from config */
function describeCondition(subtype: string, config: Record<string, any>): string {
  switch (subtype) {
    case 'priority_is':
      return config.values?.length ? `Is ${config.values.map((v: string) => v.toUpperCase()).join(' or ')}` : 'Configure priorities...';
    case 'status_is':
      return config.values?.length ? `Is ${config.values.join(' or ')}` : 'Configure statuses...';
    case 'tag_contains':
      return config.tags?.length ? `Has tag "${config.tags.join('" or "')}"` : 'Configure tags...';
    case 'assignee_set':
      return config.is_set === false ? 'No one is assigned' : 'Someone is assigned';
    case 'requester_role':
      return config.roles?.length ? `Requester is ${config.roles.join(' or ')}` : 'Configure roles...';
    case 'hours_since':
      return config.value ? `${config.field || 'Created'} ${config.operator || '>'} ${config.value}h ago` : 'Configure time...';
    case 'category_is':
      return config.category_ids?.length ? `${config.category_ids.length} categories selected` : 'Configure categories...';
    case 'location_is':
      return config.location_ids?.length ? `${config.location_ids.length} locations selected` : 'Configure locations...';
    default:
      return '';
  }
}

export function ConditionNode({ data, selected }: NodeProps) {
  const subtype = data.subtype as string;
  const config = (data.config || {}) as Record<string, any>;
  const conditions = config.conditions as { subtype: string; config: Record<string, any> }[] | undefined;
  const logic = ((config.logic as string) || 'and').toUpperCase();
  const isMulti = conditions && conditions.length > 1;

  // Single-condition path
  if (!conditions || conditions.length <= 1) {
    const c = conditions?.[0];
    const s = c ? c.subtype : subtype;
    const cfg = c ? (c.config || {}) : config;
    const label = (data.label as string) || CONDITION_LABELS[s] || s;
    const description = describeCondition(s, cfg);
    return (
      <div className={`auto-node auto-node-condition ${selected ? 'selected' : ''}`}>
        <Handle type="target" position={Position.Left} id="target" className="auto-handle" />
        <div className="auto-node-header">
          <span className="auto-node-icon"><svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M7 1L13 7L7 13L1 7Z" /></svg></span>
          <span className="auto-node-type">Condition</span>
        </div>
        <div className="auto-node-label">{label}</div>
        {description && <div className="auto-node-desc">{description}</div>}
        <Handle type="source" position={Position.Right} id="true" className="auto-handle auto-handle-true auto-handle-lg" style={{ top: '35%' }} />
        <Handle type="source" position={Position.Right} id="false" className="auto-handle auto-handle-false auto-handle-lg" style={{ top: '75%' }} />
      </div>
    );
  }

  // Multi-condition path
  const label = (data.label as string) || `${conditions.length} conditions`;
  return (
    <div className={`auto-node auto-node-condition${isMulti ? ' multi' : ''} ${selected ? 'selected' : ''}`}>
      <Handle type="target" position={Position.Left} id="target" className="auto-handle" />
      <div className="auto-node-header">
        <span className="auto-node-icon"><svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M7 1L13 7L7 13L1 7Z" /></svg></span>
        <span className="auto-node-type">Condition</span>
      </div>
      <div className="auto-node-label">{label}</div>
      <div className="auto-node-desc" style={{ fontStyle: 'normal' }}>
        {conditions.map((c, i) => {
          const desc = describeCondition(c.subtype, c.config || {}) || CONDITION_LABELS[c.subtype] || c.subtype;
          const isLast = i === conditions.length - 1;
          return (
            <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: '6px' }}>
              <span style={{ fontStyle: 'italic', color: 'var(--t-text-muted)' }}>{desc}</span>
              {!isLast && (
                <span style={{ fontWeight: 700, fontSize: '9px', letterSpacing: '0.06em', color: 'var(--t-accent)', flexShrink: 0 }}>{logic}</span>
              )}
            </div>
          );
        })}
      </div>
      <Handle type="source" position={Position.Right} id="true" className="auto-handle auto-handle-true auto-handle-lg" style={{ top: '35%' }} />
      <Handle type="source" position={Position.Right} id="false" className="auto-handle auto-handle-false auto-handle-lg" style={{ top: '75%' }} />
    </div>
  );
}
