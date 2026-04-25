BEGIN;
SET search_path = helpdesk, public;

-- New knowledge modules: Shift4, Lightspeed, Square, Oracle Simphony, Oracle Xstore
INSERT INTO knowledge_modules (slug, name, description, icon, is_active)
VALUES ('shift4', 'Shift4/SkyTab', 'Shift4 payments and SkyTab POS support', 'credit-card', true)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO knowledge_modules (slug, name, description, icon, is_active)
VALUES ('lightspeed', 'Lightspeed', 'Lightspeed restaurant POS support (K-Series, L-Series)', 'zap', true)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO knowledge_modules (slug, name, description, icon, is_active)
VALUES ('square', 'Square', 'Square POS, payments, and business management support', 'square', true)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO knowledge_modules (slug, name, description, icon, is_active)
VALUES ('oracle_simphony', 'Oracle Simphony', 'Oracle MICROS Simphony POS documentation', 'database', true)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO knowledge_modules (slug, name, description, icon, is_active)
VALUES ('oracle_xstore', 'Oracle Xstore', 'Oracle MICROS Xstore POS documentation', 'database', true)
ON CONFLICT (slug) DO NOTHING;

-- KB pipeline cron schedule (replaces n8n orchestrator)
INSERT INTO pipeline_schedules (step_name, cron_expression, enabled, payload)
VALUES ('kb_pipeline', '0 2 * * 0', true, '{}')
ON CONFLICT (step_name) DO NOTHING;

COMMIT;
