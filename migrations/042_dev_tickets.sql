-- 042: Dev tickets — ticket types, sprints, task checklists, status workflows
--
-- Adds project management capabilities alongside the existing support desk:
--   - ticket_type: 'support' (default), 'task', 'bug', 'feature'
--   - Configurable status workflows per ticket_type per tenant
--   - Sprints: time-boxed work containers owned by a team
--   - Ticket tasks: lightweight checklist items on any ticket
--   - Story points: optional effort estimation for dev tickets

-- ── Ticket type + dev fields ─────────────────────────────────────────────────
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS ticket_type TEXT NOT NULL DEFAULT 'support';
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS story_points INTEGER;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS sprint_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_tickets_type ON tickets(ticket_type);
CREATE INDEX IF NOT EXISTS idx_tickets_sprint ON tickets(sprint_id);

-- ── Sprints ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sprints (
    id          SERIAL PRIMARY KEY,
    tenant_id   INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    team_id     INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    goal        TEXT,
    start_date  DATE,
    end_date    DATE,
    status      TEXT NOT NULL DEFAULT 'planning',  -- planning, active, completed
    created_by  INTEGER REFERENCES users(id),
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sprints_tenant ON sprints(tenant_id);
CREATE INDEX IF NOT EXISTS idx_sprints_team ON sprints(team_id);
CREATE INDEX IF NOT EXISTS idx_sprints_status ON sprints(status);

-- FK from tickets to sprints (deferred so table creation order doesn't matter)
ALTER TABLE tickets ADD CONSTRAINT fk_tickets_sprint
    FOREIGN KEY (sprint_id) REFERENCES sprints(id) ON DELETE SET NULL;

-- ── Ticket status workflows ──────────────────────────────────────────────────
-- Each tenant can have custom status workflows per ticket_type.
-- If no row exists for a tenant+type, the system default is used.
CREATE TABLE IF NOT EXISTS ticket_status_workflows (
    id          SERIAL PRIMARY KEY,
    tenant_id   INTEGER REFERENCES tenants(id) ON DELETE CASCADE,
    ticket_type TEXT NOT NULL,
    statuses    JSONB NOT NULL,  -- ordered array: [{"key":"open","label":"Open","category":"active"}, ...]
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT now(),
    UNIQUE(tenant_id, ticket_type)
);

-- System defaults (tenant_id = NULL) — used when tenant has no custom workflow
INSERT INTO ticket_status_workflows (tenant_id, ticket_type, statuses) VALUES
(NULL, 'support', '[
    {"key":"open","label":"Open","category":"active"},
    {"key":"pending","label":"Pending","category":"active"},
    {"key":"resolved","label":"Resolved","category":"done"},
    {"key":"closed_not_resolved","label":"Closed (Not Resolved)","category":"done"}
]'::jsonb),
(NULL, 'task', '[
    {"key":"backlog","label":"Backlog","category":"backlog"},
    {"key":"todo","label":"To Do","category":"active"},
    {"key":"in_progress","label":"In Progress","category":"active"},
    {"key":"in_review","label":"In Review","category":"active"},
    {"key":"testing","label":"Testing","category":"active"},
    {"key":"done","label":"Done","category":"done"},
    {"key":"cancelled","label":"Cancelled","category":"done"}
]'::jsonb),
(NULL, 'bug', '[
    {"key":"backlog","label":"Backlog","category":"backlog"},
    {"key":"todo","label":"To Do","category":"active"},
    {"key":"in_progress","label":"In Progress","category":"active"},
    {"key":"in_review","label":"In Review","category":"active"},
    {"key":"testing","label":"Testing","category":"active"},
    {"key":"done","label":"Done","category":"done"},
    {"key":"cancelled","label":"Cancelled","category":"done"}
]'::jsonb),
(NULL, 'feature', '[
    {"key":"backlog","label":"Backlog","category":"backlog"},
    {"key":"todo","label":"To Do","category":"active"},
    {"key":"in_progress","label":"In Progress","category":"active"},
    {"key":"in_review","label":"In Review","category":"active"},
    {"key":"testing","label":"Testing","category":"active"},
    {"key":"done","label":"Done","category":"done"},
    {"key":"cancelled","label":"Cancelled","category":"done"}
]'::jsonb)
ON CONFLICT DO NOTHING;

-- ── Ticket tasks (checklist items) ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ticket_tasks (
    id          SERIAL PRIMARY KEY,
    ticket_id   INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    assignee_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    status      TEXT NOT NULL DEFAULT 'todo',  -- todo, in_progress, done
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ticket_tasks_ticket ON ticket_tasks(ticket_id);
