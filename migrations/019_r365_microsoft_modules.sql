BEGIN;
SET search_path = helpdesk, public;

INSERT INTO knowledge_modules (slug, name, description, icon, is_active)
VALUES ('r365', 'Restaurant365', 'Restaurant365 operations and accounting platform documentation', 'utensils', true)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO knowledge_modules (slug, name, description, icon, is_active)
VALUES ('ms_outlook', 'Microsoft Outlook', 'Microsoft Outlook email and calendar support', 'mail', true)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO knowledge_modules (slug, name, description, icon, is_active)
VALUES ('ms_teams', 'Microsoft Teams', 'Microsoft Teams collaboration and meetings support', 'users', true)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO knowledge_modules (slug, name, description, icon, is_active)
VALUES ('ms_excel', 'Microsoft Excel', 'Microsoft Excel spreadsheet and data analysis support', 'table', true)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO knowledge_modules (slug, name, description, icon, is_active)
VALUES ('ms_sharepoint', 'Microsoft SharePoint', 'Microsoft SharePoint document management and collaboration support', 'folder', true)
ON CONFLICT (slug) DO NOTHING;

COMMIT;
