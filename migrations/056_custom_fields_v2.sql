-- Migration 056: Custom fields v2
-- Adds category association + split required-to-create / required-to-close modes
SET search_path TO helpdesk, public;

-- Category scoping: field can be tied to a specific problem category
ALTER TABLE custom_field_definitions
  ADD COLUMN IF NOT EXISTS category_id INTEGER REFERENCES problem_categories(id) ON DELETE SET NULL;

-- Split "required" into two distinct enforcement points
ALTER TABLE custom_field_definitions
  ADD COLUMN IF NOT EXISTS is_required_to_create BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS is_required_to_close  BOOLEAN DEFAULT FALSE;

-- Back-fill from old is_required flag (required_to_create = old required)
UPDATE custom_field_definitions SET is_required_to_create = is_required WHERE is_required = true;

CREATE INDEX IF NOT EXISTS idx_custom_field_definitions_category
  ON custom_field_definitions(category_id) WHERE category_id IS NOT NULL;
