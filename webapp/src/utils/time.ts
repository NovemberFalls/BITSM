/**
 * Time/duration utilities for ticket display.
 */

export function formatDuration(seconds: number): string {
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ${minutes % 60}m`;
  const days = Math.floor(hours / 24);
  if (days === 1) return `1d ${hours % 24}h`;
  return `${days}d ${hours % 24}h`;
}

export function formatSlaRemaining(ticket: { sla_due_at: string | null; sla_breached: boolean }): string {
  if (!ticket.sla_due_at) return 'No SLA';
  const due = new Date(ticket.sla_due_at).getTime();
  const now = Date.now();
  const diffSec = Math.floor((due - now) / 1000);

  if (ticket.sla_breached || diffSec < 0) {
    return `Overdue by ${formatDuration(Math.abs(diffSec))}`;
  }
  return `${formatDuration(diffSec)} remaining`;
}

export function slaStatusColor(status: string): string {
  switch (status) {
    case 'breached': return 'var(--t-error, #ef4444)';
    case 'at_risk': return 'var(--t-warning, #f59e0b)';
    case 'on_track': return 'var(--t-success, #10b981)';
    default: return 'var(--t-text-muted, #6b7280)';
  }
}

export function slaStatusLabel(status: string): string {
  switch (status) {
    case 'breached': return 'Breached';
    case 'at_risk': return 'At Risk';
    case 'on_track': return 'On Track';
    default: return 'No SLA';
  }
}

export function timeAgo(dateStr: string): string {
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const diff = Math.floor((now - then) / 1000);
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  const days = Math.floor(diff / 86400);
  if (days === 1) return '1d ago';
  return `${days}d ago`;
}
