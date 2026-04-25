-- Migration 072: Add subject_format to form_templates
-- Allows admins to define how auto-generated ticket subjects look
-- using {{field_key}} variables, e.g. "Carrot Bucks — {{employee_full_name}}"
-- Falls back to template name if not set.

SET search_path TO helpdesk, public;

ALTER TABLE form_templates ADD COLUMN IF NOT EXISTS subject_format TEXT;
