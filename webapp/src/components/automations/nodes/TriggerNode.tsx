import { Handle, Position, type NodeProps } from '@xyflow/react';

const TRIGGER_LABELS: Record<string, string> = {
  ticket_created: 'Ticket Created',
  status_changed: 'Status Changed',
  priority_changed: 'Priority Changed',
  comment_added: 'Comment Added',
  assignee_changed: 'Assignee Changed',
  tag_added: 'Tag Added',
  sla_breached: 'SLA Breached',
  schedule: 'Schedule',
};

function describeTrigger(subtype: string, config: Record<string, any>): string {
  switch (subtype) {
    case 'ticket_created': return 'When a new ticket is created';
    case 'status_changed':
      if (config.from && config.to) return `When status changes from ${config.from} to ${config.to}`;
      if (config.to) return `When status changes to ${config.to}`;
      if (config.from) return `When status changes from ${config.from}`;
      return 'When ticket status changes';
    case 'priority_changed':
      if (config.to) return `When priority changes to ${config.to.toUpperCase()}`;
      return 'When ticket priority changes';
    case 'comment_added':
      if (config.comment_type === 'public') return 'When a public comment is added';
      if (config.comment_type === 'internal') return 'When an internal note is added';
      return 'When a comment is added';
    case 'assignee_changed': return 'When ticket is assigned or reassigned';
    case 'tag_added':
      return config.tag ? `When tag "${config.tag}" is added` : 'When any tag is added';
    case 'sla_breached': return 'When SLA deadline is breached';
    case 'schedule':
      return config.cron ? `Runs on schedule: ${config.cron}` : 'Configure cron schedule...';
    default: return '';
  }
}

export function TriggerNode({ data, selected }: NodeProps) {
  const subtype = data.subtype as string;
  const label = (data.label as string) || TRIGGER_LABELS[subtype] || subtype;
  const config = (data.config || {}) as Record<string, any>;
  const description = describeTrigger(subtype, config);

  return (
    <div className={`auto-node auto-node-trigger ${selected ? 'selected' : ''}`}>
      <Handle type="target" position={Position.Left} id="target" className="auto-handle" style={{ visibility: 'hidden' }} />
      <div className="auto-node-header">
        <span className="auto-node-icon">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="currentColor">
            <path d="M7.5 1L3 8h3.5L6 13l5-7H7.5L8 1z" />
          </svg>
        </span>
        <span className="auto-node-type">Trigger</span>
      </div>
      <div className="auto-node-label">{label}</div>
      {description && <div className="auto-node-desc">{description}</div>}
      <Handle type="source" position={Position.Right} id="default" className="auto-handle" />
    </div>
  );
}
