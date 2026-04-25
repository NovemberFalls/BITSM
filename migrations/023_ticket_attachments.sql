-- Migration 023: Ticket attachments
-- Adds file attachment support to tickets and comments.

BEGIN;

CREATE TABLE IF NOT EXISTS helpdesk.ticket_attachments (
    id              SERIAL PRIMARY KEY,
    ticket_id       INT NOT NULL REFERENCES helpdesk.tickets(id),
    comment_id      INT REFERENCES helpdesk.ticket_comments(id),
    filename        VARCHAR(255) NOT NULL,
    stored_filename VARCHAR(255) NOT NULL,
    file_size       INT NOT NULL,
    content_type    VARCHAR(100) NOT NULL,
    uploaded_by     INT NOT NULL REFERENCES helpdesk.users(id),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_attachments_ticket ON helpdesk.ticket_attachments(ticket_id);
CREATE INDEX IF NOT EXISTS idx_attachments_comment ON helpdesk.ticket_attachments(comment_id);

COMMIT;
