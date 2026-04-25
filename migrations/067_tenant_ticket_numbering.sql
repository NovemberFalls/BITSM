-- SOC 2 / Feature: Per-tenant ticket numbering with configurable prefix
-- Each tenant gets their own sequential counter and optional prefix (default: TKT)
-- Format: {prefix}-{seq:05d}  e.g. ACME-00042, IT-00100
SET search_path TO helpdesk;

ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS ticket_prefix VARCHAR(20) NOT NULL DEFAULT 'TKT',
    ADD COLUMN IF NOT EXISTS ticket_seq_last INTEGER NOT NULL DEFAULT 0;

-- Seed ticket_seq_last for each tenant from their current max ticket number.
-- Existing tickets have format 'TKT-#####' from the global sequence.
-- After this migration, new tickets start from each tenant's own max + 1.
UPDATE tenants t
SET ticket_seq_last = COALESCE(
    (
        SELECT MAX(
            CASE
                WHEN ticket_number ~ '^[A-Z]+-[0-9]+$'
                THEN CAST(SPLIT_PART(ticket_number, '-', 2) AS INTEGER)
                ELSE 0
            END
        )
        FROM tickets
        WHERE tenant_id = t.id
    ),
    0
);
