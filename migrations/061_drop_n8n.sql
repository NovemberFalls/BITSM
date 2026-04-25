-- Migration 061: Drop n8n_execution_log
-- n8n was fully removed from the stack. Table was added in migration 017,
-- has 0 rows, and is not referenced anywhere in Python code.

DROP TABLE IF EXISTS helpdesk.n8n_execution_log;
