-- 043: Team-scoped categories + Atlas team triage support
--
-- 1. Make team description NOT NULL (required for Atlas triage context)
-- 2. Add team_id to problem_categories (categories can belong to a team)
-- 3. Add suggested_team_id to atlas_engagements (Atlas team suggestion)
-- 4. Add default_team_id to portal card metadata support (stored in tenant settings JSONB)

-- Team description required for Atlas context
UPDATE teams SET description = name WHERE description IS NULL OR description = '';
ALTER TABLE teams ALTER COLUMN description SET NOT NULL;
ALTER TABLE teams ALTER COLUMN description SET DEFAULT '';

-- Categories can belong to a team
ALTER TABLE problem_categories ADD COLUMN IF NOT EXISTS team_id INTEGER REFERENCES teams(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_problem_categories_team ON problem_categories(team_id);

-- Atlas team suggestion tracking
ALTER TABLE atlas_engagements ADD COLUMN IF NOT EXISTS suggested_team_id INTEGER REFERENCES teams(id) ON DELETE SET NULL;
ALTER TABLE atlas_engagements ADD COLUMN IF NOT EXISTS team_confidence REAL;
