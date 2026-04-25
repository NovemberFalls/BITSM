-- 029: Make tenant_id NOT NULL on tenant_token_usage
-- All usage must be attributed to a tenant for invoicing.
-- Backfill was applied manually before this constraint.

SET search_path = helpdesk, public;

-- Backfill any remaining NULLs from the ticket's tenant_id
UPDATE tenant_token_usage u
SET tenant_id = t.tenant_id
FROM tickets t
WHERE u.ticket_id = t.id
  AND u.tenant_id IS NULL;

-- Now enforce NOT NULL
ALTER TABLE tenant_token_usage ALTER COLUMN tenant_id SET NOT NULL;
