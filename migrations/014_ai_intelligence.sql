-- Migration 014: AI Intelligence — Module restructure, Atlas engagement, audit queue
-- Renames ai_chat → ai, adds sub-feature toggles, audit queue, engagement tracking,
-- knowledge gap detection, effort scoring, and smart routing.

SET search_path TO helpdesk, public;

BEGIN;

-- ============================================================
-- 1. Rename ai_chat module → ai, update description
-- ============================================================
UPDATE knowledge_modules
SET slug = 'ai',
    name = 'AI',
    description = 'Atlas AI — ticket review, agent chat, client chat, phone service',
    icon = 'cpu'
WHERE slug = 'ai_chat';

-- ============================================================
-- 2. Module features (sub-toggles per module)
-- ============================================================
CREATE TABLE IF NOT EXISTS module_features (
    id          SERIAL PRIMARY KEY,
    module_id   INT NOT NULL REFERENCES knowledge_modules(id) ON DELETE CASCADE,
    slug        TEXT NOT NULL,
    name        TEXT NOT NULL,
    description TEXT,
    icon        TEXT DEFAULT 'toggle-right',
    sort_order  INT DEFAULT 0,
    is_active   BOOLEAN DEFAULT true,
    created_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE(module_id, slug)
);

-- Seed AI sub-features
INSERT INTO module_features (module_id, slug, name, description, icon, sort_order)
SELECT km.id, f.slug, f.name, f.description, f.icon, f.sort_order
FROM knowledge_modules km
CROSS JOIN (VALUES
    ('ticket_review', 'AI Ticket Review',    'Atlas auto-engages tickets, audits on close, tags, validates categories', 'clipboard-check', 0),
    ('agent_chat',    'Agent Facing Chat',   'Atlas chat available to agents from tickets and backend',                'message-square',  1),
    ('client_chat',   'Client Facing Chat',  'Atlas chat widget in customer portal',                                  'message-circle',  2),
    ('phone_service', 'Phone Service',       'AI-powered phone support (future scope)',                                'phone',           3)
) AS f(slug, name, description, icon, sort_order)
WHERE km.slug = 'ai'
ON CONFLICT (module_id, slug) DO NOTHING;

-- Tenant-level feature toggles
CREATE TABLE IF NOT EXISTS tenant_module_features (
    id          SERIAL PRIMARY KEY,
    tenant_id   INT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    feature_id  INT NOT NULL REFERENCES module_features(id) ON DELETE CASCADE,
    enabled     BOOLEAN DEFAULT true,
    enabled_at  TIMESTAMPTZ DEFAULT now(),
    enabled_by  TEXT,
    UNIQUE(tenant_id, feature_id)
);

-- When a tenant enables the AI module, auto-enable agent_chat and client_chat
-- (ticket_review and phone_service start disabled — opt-in)
-- This is handled by application logic, not by migration.

-- ============================================================
-- 3. Atlas ticket engagements
-- ============================================================
CREATE TABLE IF NOT EXISTS atlas_engagements (
    id              SERIAL PRIMARY KEY,
    ticket_id       INT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    tenant_id       INT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    conversation_id INT REFERENCES ai_conversations(id),
    status          TEXT NOT NULL DEFAULT 'active',  -- active, passive, closed
    engagement_type TEXT NOT NULL DEFAULT 'l1',      -- l1, l2, audit
    started_at      TIMESTAMPTZ DEFAULT now(),
    human_took_over BOOLEAN DEFAULT false,
    human_took_over_at TIMESTAMPTZ,
    resolved_by_ai  BOOLEAN DEFAULT false,
    summary         TEXT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT atlas_engagements_status_check
        CHECK (status IN ('active', 'passive', 'closed')),
    CONSTRAINT atlas_engagements_type_check
        CHECK (engagement_type IN ('l1', 'l2', 'audit'))
);

CREATE INDEX idx_atlas_engagements_ticket ON atlas_engagements(ticket_id);
CREATE INDEX idx_atlas_engagements_tenant ON atlas_engagements(tenant_id);
CREATE INDEX idx_atlas_engagements_status ON atlas_engagements(tenant_id, status);

-- ============================================================
-- 4. Ticket audit queue
-- ============================================================
CREATE TABLE IF NOT EXISTS ticket_audit_queue (
    id                  SERIAL PRIMARY KEY,
    ticket_id           INT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    tenant_id           INT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    queue_type          TEXT NOT NULL DEFAULT 'human_resolved',
        -- auto_resolved: Atlas closed it, needs spot-check
        -- human_resolved: Agent closed it, Atlas audits tags/category
        -- low_confidence: Atlas unsure about tags/category, human reviews
        -- kba_candidate: Resolution was novel, suggest KBA creation
    status              TEXT NOT NULL DEFAULT 'pending',
        -- pending, reviewed, approved, dismissed
    ai_suggested_tags   TEXT[] DEFAULT '{}',
    ai_suggested_category_id INT REFERENCES problem_categories(id),
    ai_category_confidence REAL,
    resolution_score    REAL,       -- 0-1 quality score
    resolution_notes    TEXT,       -- Atlas explanation of score
    kba_draft           TEXT,       -- Draft KBA content if kba_candidate
    matched_article_id  INT REFERENCES documents(id),
    reviewed_by         INT REFERENCES users(id),
    reviewed_at         TIMESTAMPTZ,
    auto_close_at       TIMESTAMPTZ,  -- When to auto-close if unreviewed
    created_at          TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT audit_queue_type_check
        CHECK (queue_type IN ('auto_resolved', 'human_resolved', 'low_confidence', 'kba_candidate')),
    CONSTRAINT audit_queue_status_check
        CHECK (status IN ('pending', 'reviewed', 'approved', 'dismissed'))
);

CREATE INDEX idx_audit_queue_tenant ON ticket_audit_queue(tenant_id);
CREATE INDEX idx_audit_queue_status ON ticket_audit_queue(tenant_id, status);
CREATE INDEX idx_audit_queue_auto_close ON ticket_audit_queue(auto_close_at) WHERE status = 'pending';

-- ============================================================
-- 5. Knowledge gaps
-- ============================================================
CREATE TABLE IF NOT EXISTS knowledge_gaps (
    id              SERIAL PRIMARY KEY,
    tenant_id       INT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    topic           TEXT NOT NULL,
    ticket_count    INT DEFAULT 1,
    sample_tickets  INT[] DEFAULT '{}',  -- Array of ticket IDs
    suggested_title TEXT,
    status          TEXT DEFAULT 'detected',
        -- detected, acknowledged, article_created, dismissed
    created_article_id INT REFERENCES documents(id),
    detected_at     TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT knowledge_gaps_status_check
        CHECK (status IN ('detected', 'acknowledged', 'article_created', 'dismissed'))
);

CREATE INDEX idx_knowledge_gaps_tenant ON knowledge_gaps(tenant_id);

-- ============================================================
-- 6. Ticket resolution metrics
-- ============================================================
CREATE TABLE IF NOT EXISTS ticket_metrics (
    id                  SERIAL PRIMARY KEY,
    ticket_id           INT NOT NULL UNIQUE REFERENCES tickets(id) ON DELETE CASCADE,
    tenant_id           INT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,

    -- Customer Effort Score (inferred)
    effort_score        REAL,           -- 1-5 (1=low effort, 5=high effort)
    reply_count         INT DEFAULT 0,
    requester_replies   INT DEFAULT 0,
    agent_replies       INT DEFAULT 0,
    sentiment_shifts    INT DEFAULT 0,

    -- First Contact Resolution
    resolved_first_contact BOOLEAN,
    escalation_count    INT DEFAULT 0,

    -- Routing
    suggested_assignee_id INT REFERENCES users(id),
    routing_confidence  REAL,
    routing_reason      TEXT,

    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_ticket_metrics_tenant ON ticket_metrics(tenant_id);

-- ============================================================
-- 7. Add audit settings to tenant settings JSONB
-- ============================================================
-- (Handled in application code via tenants.settings JSONB)
-- Keys: ai_audit_auto_close_days, ai_audit_enabled

COMMIT;
