import { Handle, Position, type NodeProps } from '@xyflow/react';

const ACTION_LABELS: Record<string, string> = {
  add_tag: 'Add Tag',
  assign_team: 'Assign Team',
  assign_to: 'Assign To Agent',
  change_priority: 'Change Priority',
  change_status: 'Change Status',
  do_nothing: 'Do Nothing',
  email_group: 'Email Group',
  post_comment: 'Post Comment',
  remove_tag: 'Remove Tag',
  send_notification: 'Send Notification',
  set_custom_field: 'Set Custom Field',
  webhook: 'Webhook',
};

function describeAction(subtype: string, config: Record<string, any>): string {
  switch (subtype) {
    case 'assign_to':
      return config.agent_name ? `Assign to ${config.agent_name}` : 'Select an agent...';
    case 'change_priority':
      return config.priority ? `Set priority to ${config.priority.toUpperCase()}` : 'Select priority...';
    case 'change_status':
      return config.status ? `Set status to ${config.status}` : 'Select status...';
    case 'add_tag':
      return config.tag ? `Add tag "${config.tag}"` : 'Enter tag...';
    case 'remove_tag':
      return config.tag ? `Remove tag "${config.tag}"` : 'Enter tag...';
    case 'post_comment':
      return config.content ? `Post: "${config.content.slice(0, 40)}${config.content.length > 40 ? '...' : ''}"` : 'Enter comment...';
    case 'send_notification':
      return config.channel ? `Notify via ${config.channel}` : 'Configure notification...';
    case 'webhook':
      if (config.url) { try { return `${config.method || 'POST'} ${new URL(config.url).hostname}`; } catch { return config.url; } }
      return 'Configure webhook URL...';
    case 'assign_team':
      return config.team_name ? `Assign to ${config.team_name}` : 'Select a team...';
    case 'email_group':
      return config.group_name ? `Email ${config.group_name}` : 'Select a group...';
    case 'set_custom_field':
      if (config.field_key) return `Set ${config.field_key} = ${JSON.stringify(config.value ?? '')}`;
      return 'Select a field...';
    case 'do_nothing':
      return 'End of path — no action taken';
    default: return '';
  }
}

export function ActionNode({ data, selected }: NodeProps) {
  const subtype = data.subtype as string;
  const label = (data.label as string) || ACTION_LABELS[subtype] || subtype;
  const config = (data.config || {}) as Record<string, any>;
  const description = describeAction(subtype, config);

  return (
    <div className={`auto-node auto-node-action ${subtype === 'do_nothing' ? 'auto-node-noop' : ''} ${selected ? 'selected' : ''}`}>
      <Handle type="target" position={Position.Left} id="target" className="auto-handle" />
      <div className="auto-node-header">
        <span className="auto-node-icon">
          {subtype === 'do_nothing' ? (
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
              <circle cx="7" cy="7" r="5" />
              <path d="M5 5l4 4M9 5l-4 4" />
            </svg>
          ) : (
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
              <circle cx="7" cy="7" r="5" />
              <path d="M7 4.5V7L9 8.5" />
            </svg>
          )}
        </span>
        <span className="auto-node-type">Action</span>
      </div>
      <div className="auto-node-label">{label}</div>
      {description && <div className="auto-node-desc">{description}</div>}
      <Handle type="source" position={Position.Right} id="default" className="auto-handle" />
    </div>
  );
}
