-- Migration 055: Custom Field Definitions + Ticket Values
SET search_path TO helpdesk, public;
-- Allows tenant admins to define per-ticket-type custom fields
-- with required enforcement, agent-facing / customer-facing visibility,
-- and full Atlas + workflow automation awareness.

-- Field definitions (tenant-scoped schema)
CREATE TABLE IF NOT EXISTS custom_field_definitions (
    id           SERIAL PRIMARY KEY,
    tenant_id    INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    description  TEXT,
    field_key    TEXT NOT NULL,   -- snake_case slug, auto-generated from name
    field_type   TEXT NOT NULL CHECK (field_type IN (
                     'text', 'textarea', 'number', 'select',
                     'multi_select', 'checkbox', 'date', 'url')),
    options      JSONB    DEFAULT '[]'::jsonb,  -- [{label, value}] for select/multi_select
    applies_to   TEXT[]   DEFAULT ARRAY['support','task','bug','feature']::text[],
    is_required          BOOLEAN DEFAULT FALSE,
    is_customer_facing   BOOLEAN DEFAULT FALSE, -- end_users can see/fill
    is_agent_facing      BOOLEAN DEFAULT TRUE,  -- agents/admins can see/fill
    sort_order   INTEGER  DEFAULT 0,
    is_active    BOOLEAN  DEFAULT TRUE,
    created_at   TIMESTAMPTZ DEFAULT now(),
    updated_at   TIMESTAMPTZ DEFAULT now(),
    UNIQUE(tenant_id, field_key)
);

-- Per-ticket values (upserted on save)
CREATE TABLE IF NOT EXISTS ticket_custom_field_values (
    id        SERIAL PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    field_id  INTEGER NOT NULL REFERENCES custom_field_definitions(id) ON DELETE CASCADE,
    value     JSONB,
    set_by    INTEGER REFERENCES users(id),
    set_at    TIMESTAMPTZ DEFAULT now(),
    UNIQUE(ticket_id, field_id)
);

CREATE INDEX IF NOT EXISTS idx_ticket_custom_field_values_ticket
    ON ticket_custom_field_values(ticket_id);

CREATE INDEX IF NOT EXISTS idx_custom_field_definitions_tenant
    ON custom_field_definitions(tenant_id, is_active);
