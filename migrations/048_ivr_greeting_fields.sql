-- Migration 048: Add IVR greeting text fields to phone_configs
-- Allows tenants to customize the bilingual IVR greeting message
-- Also populates assigned_phone_number for tenant 1 (was only in env var)

BEGIN;

-- Add IVR greeting columns with sensible defaults
ALTER TABLE helpdesk.phone_configs
  ADD COLUMN IF NOT EXISTS ivr_greeting_en TEXT,
  ADD COLUMN IF NOT EXISTS ivr_greeting_es TEXT;

-- Populate the phone number for tenant 1 (currently only in DEV_TWILIO_PHONE_NUMBER env var)
UPDATE helpdesk.phone_configs
  SET assigned_phone_number = '+17865511510'
  WHERE tenant_id = 1
    AND (assigned_phone_number IS NULL OR assigned_phone_number = '');

COMMIT;
