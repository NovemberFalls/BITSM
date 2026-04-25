BEGIN;
SET search_path = helpdesk, public;

INSERT INTO knowledge_modules (slug, name, description, icon, is_active)
VALUES ('sonos', 'Sonos', 'Sonos speaker and audio system support', 'speaker', true)
ON CONFLICT (slug) DO NOTHING;

COMMIT;
