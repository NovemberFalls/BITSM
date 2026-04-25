-- 052: Work item fields — acceptance criteria, parent hierarchy, numbering, ranking
--
-- Phase 1: acceptance_criteria for dev items
-- Phase 2: parent_id for Epic→Story→Task→Sub-task hierarchy, allowed_parent_slugs
-- Phase 3: work_item_number (WI-#####), sort_order, sprint capacity

SET search_path TO helpdesk, public;

-- Phase 1: Acceptance criteria
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS acceptance_criteria TEXT;

-- Phase 2: Work item hierarchy (separate from parent_ticket_id which is incident linking)
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS parent_id INTEGER REFERENCES tickets(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_tickets_parent_id ON tickets(parent_id) WHERE parent_id IS NOT NULL;

-- Hierarchy rules on work_item_types
ALTER TABLE work_item_types ADD COLUMN IF NOT EXISTS allowed_parent_slugs TEXT[];

UPDATE work_item_types SET allowed_parent_slugs = '{}'::TEXT[] WHERE slug = 'epic' AND tenant_id IS NULL;
UPDATE work_item_types SET allowed_parent_slugs = '{epic}'::TEXT[] WHERE slug = 'story' AND tenant_id IS NULL;
UPDATE work_item_types SET allowed_parent_slugs = '{story,epic}'::TEXT[] WHERE slug = 'task' AND tenant_id IS NULL;
UPDATE work_item_types SET allowed_parent_slugs = '{story,epic}'::TEXT[] WHERE slug = 'bug' AND tenant_id IS NULL;
UPDATE work_item_types SET allowed_parent_slugs = '{task,story,epic}'::TEXT[] WHERE slug = 'sub-task' AND tenant_id IS NULL;

-- Phase 3: Work item numbering
CREATE SEQUENCE IF NOT EXISTS work_item_number_seq START WITH 1;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS work_item_number TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_work_item_number ON tickets(work_item_number) WHERE work_item_number IS NOT NULL;

-- Backlog ranking
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS sort_order INTEGER;
CREATE INDEX IF NOT EXISTS idx_tickets_sort_order ON tickets(sort_order) WHERE sort_order IS NOT NULL;

-- Sprint capacity
ALTER TABLE sprints ADD COLUMN IF NOT EXISTS capacity_points INTEGER;
