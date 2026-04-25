-- 025: Phone helpdesk — platform-managed credential mode
-- Adds credentials_mode ('platform'|'byok'), the platform-purchased phone number,
-- and the Twilio number SID so we can release it on BYOK migration.

BEGIN;

ALTER TABLE helpdesk.phone_configs
    ADD COLUMN IF NOT EXISTS credentials_mode            TEXT NOT NULL DEFAULT 'platform',
    ADD COLUMN IF NOT EXISTS assigned_phone_number       TEXT,
    ADD COLUMN IF NOT EXISTS platform_twilio_number_sid  TEXT;

COMMENT ON COLUMN helpdesk.phone_configs.credentials_mode IS
    'platform = BITSM supplies EL + Twilio keys; byok = tenant supplies their own';
COMMENT ON COLUMN helpdesk.phone_configs.assigned_phone_number IS
    'E.164 number purchased on platform Twilio account for this tenant';
COMMENT ON COLUMN helpdesk.phone_configs.platform_twilio_number_sid IS
    'Twilio IncomingPhoneNumber SID — needed to release on BYOK migration';

COMMIT;
