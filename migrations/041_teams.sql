-- 041: Teams — work units within a tenant
-- Teams are distinct from RBAC groups. Groups control permissions; teams control work assignment.
-- A tenant can have multiple teams (e.g., "Support", "Engineering", "DevOps").
-- Tickets are assigned to a team AND optionally to an individual within that team.

CREATE TABLE IF NOT EXISTS teams (
    id          SERIAL PRIMARY KEY,
    tenant_id   INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL,
    description TEXT,
    lead_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    is_active   BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT now(),
    UNIQUE(tenant_id, slug)
);

CREATE INDEX IF NOT EXISTS idx_teams_tenant ON teams(tenant_id);

-- Team membership — users can belong to multiple teams
CREATE TABLE IF NOT EXISTS team_members (
    id       SERIAL PRIMARY KEY,
    team_id  INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role     TEXT NOT NULL DEFAULT 'member',  -- 'member', 'lead'
    added_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    UNIQUE(team_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_team_members_team ON team_members(team_id);
CREATE INDEX IF NOT EXISTS idx_team_members_user ON team_members(user_id);

-- Ticket team assignment
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS team_id INTEGER REFERENCES teams(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_tickets_team ON tickets(team_id);
