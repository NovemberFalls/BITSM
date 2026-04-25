BEGIN;
SET search_path = helpdesk, public;

-- New knowledge modules: Bill.com and Paytronix (Salesforce Experience Cloud)
INSERT INTO knowledge_modules (slug, name, description, icon, is_active)
VALUES ('bill_com', 'Bill.com', 'BILL accounts payable, receivable, payments, and vendor management support', 'file-text', true)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO knowledge_modules (slug, name, description, icon, is_active)
VALUES ('paytronix', 'Paytronix', 'Paytronix loyalty, rewards, and online ordering support', 'gift', true)
ON CONFLICT (slug) DO NOTHING;

COMMIT;
