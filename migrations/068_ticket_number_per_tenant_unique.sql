-- Fix: per-tenant ticket numbering requires per-tenant uniqueness
-- The old global UNIQUE(ticket_number) constraint prevents two different tenants
-- from both having TKT-00001. Replace with UNIQUE(tenant_id, ticket_number).
-- NULL ticket_numbers (dev/work-item tickets) are unaffected — NULLs are never
-- considered equal in PostgreSQL unique constraints, so dev tickets still work.
SET search_path TO helpdesk;

ALTER TABLE tickets
    DROP CONSTRAINT IF EXISTS tickets_ticket_number_key,
    ADD CONSTRAINT tickets_ticket_number_tenant_key UNIQUE (tenant_id, ticket_number);
