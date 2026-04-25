-- 065_ticket_activity.sql: Ticket activity timeline
-- Tracks status changes, priority changes, assignments, category changes, etc.

CREATE TABLE IF NOT EXISTS helpdesk.ticket_activity (
    id SERIAL PRIMARY KEY,
    tenant_id INT NOT NULL REFERENCES helpdesk.tenants(id),
    ticket_id INT NOT NULL REFERENCES helpdesk.tickets(id),
    user_id INT REFERENCES helpdesk.users(id),
    activity_type TEXT NOT NULL,  -- 'status_changed', 'priority_changed', 'assigned', 'team_assigned', 'category_changed', 'created', 'comment_added'
    old_value TEXT,
    new_value TEXT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ticket_activity_ticket ON helpdesk.ticket_activity(ticket_id, created_at);
