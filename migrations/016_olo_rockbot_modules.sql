BEGIN;
SET search_path = helpdesk, public;

INSERT INTO knowledge_modules (slug, name, description, icon, is_active)
VALUES ('olo', 'Olo', 'Olo online ordering and delivery platform support', 'shopping-cart', true)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO knowledge_modules (slug, name, description, icon, is_active)
VALUES ('rockbot', 'Rockbot', 'Rockbot digital signage and music platform support', 'music', true)
ON CONFLICT (slug) DO NOTHING;

COMMIT;
