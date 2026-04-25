-- Helpdesk Core Schema
-- Database: helpdesk (isolated from any other databases on the same cluster)

CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS helpdesk;

-- ============================================================
-- TENANTS
-- ============================================================
CREATE TABLE helpdesk.tenants (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    slug            TEXT UNIQUE NOT NULL,
    domain          TEXT,
    logo_url        TEXT,
    settings        JSONB DEFAULT '{}',
    is_active       BOOLEAN DEFAULT true,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- USERS (support agents + end-users)
-- ============================================================
CREATE TABLE helpdesk.users (
    id              SERIAL PRIMARY KEY,
    tenant_id       INT REFERENCES helpdesk.tenants(id),
    email           TEXT NOT NULL,
    name            TEXT,
    role            TEXT NOT NULL CHECK (role IN ('super_admin', 'tenant_admin', 'agent', 'end_user')),
    provider        TEXT,
    avatar_url      TEXT,
    preferences     JSONB DEFAULT '{}',
    is_active       BOOLEAN DEFAULT true,
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE(tenant_id, email)
);

CREATE INDEX idx_users_email ON helpdesk.users(email);

-- ============================================================
-- KNOWLEDGE MODULES (the core catalog)
-- ============================================================
CREATE TABLE helpdesk.knowledge_modules (
    id              SERIAL PRIMARY KEY,
    slug            TEXT UNIQUE NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT,
    icon            TEXT,
    doc_count       INT DEFAULT 0,
    is_active       BOOLEAN DEFAULT true,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- TENANT <-> MODULE ENABLEMENT
-- ============================================================
CREATE TABLE helpdesk.tenant_modules (
    id              SERIAL PRIMARY KEY,
    tenant_id       INT REFERENCES helpdesk.tenants(id) ON DELETE CASCADE,
    module_id       INT REFERENCES helpdesk.knowledge_modules(id) ON DELETE CASCADE,
    enabled_at      TIMESTAMPTZ DEFAULT now(),
    enabled_by      TEXT,
    UNIQUE(tenant_id, module_id)
);

-- ============================================================
-- KNOWLEDGE DOCUMENTS
-- ============================================================
CREATE TABLE helpdesk.documents (
    id              SERIAL PRIMARY KEY,
    module_id       INT REFERENCES helpdesk.knowledge_modules(id) ON DELETE CASCADE,
    source_file     TEXT,
    source_url      TEXT,
    title           TEXT,
    content         TEXT NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_documents_module ON helpdesk.documents(module_id);

-- ============================================================
-- DOCUMENT CHUNKS (for RAG retrieval via pgvector)
-- ============================================================
CREATE TABLE helpdesk.document_chunks (
    id              SERIAL PRIMARY KEY,
    document_id     INT REFERENCES helpdesk.documents(id) ON DELETE CASCADE,
    module_id       INT REFERENCES helpdesk.knowledge_modules(id) ON DELETE CASCADE,
    chunk_index     INT,
    content         TEXT NOT NULL,
    token_count     INT,
    embedding       vector(1536),
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_chunks_module ON helpdesk.document_chunks(module_id);
CREATE INDEX idx_chunks_document ON helpdesk.document_chunks(document_id);
-- IVFFlat index created after initial data load (needs rows to train):
-- CREATE INDEX idx_chunks_embedding ON helpdesk.document_chunks
--     USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- ============================================================
-- SLA POLICIES
-- ============================================================
CREATE TABLE helpdesk.sla_policies (
    id                      SERIAL PRIMARY KEY,
    tenant_id               INT REFERENCES helpdesk.tenants(id) ON DELETE CASCADE,
    name                    TEXT NOT NULL,
    priority                TEXT NOT NULL,
    first_response_minutes  INT,
    resolution_minutes      INT,
    escalation_minutes      INT,
    business_hours_only     BOOLEAN DEFAULT true,
    created_at              TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_sla_tenant ON helpdesk.sla_policies(tenant_id);

-- ============================================================
-- TICKETS
-- ============================================================
CREATE TABLE helpdesk.tickets (
    id              SERIAL PRIMARY KEY,
    tenant_id       INT REFERENCES helpdesk.tenants(id),
    ticket_number   TEXT UNIQUE NOT NULL,
    subject         TEXT NOT NULL,
    description     TEXT,
    status          TEXT DEFAULT 'open'
                    CHECK (status IN ('open', 'pending', 'in_progress',
                           'waiting_on_customer', 'escalated', 'resolved', 'closed')),
    priority        TEXT DEFAULT 'medium'
                    CHECK (priority IN ('low', 'medium', 'high', 'urgent')),
    category        TEXT,
    tags            TEXT[] DEFAULT '{}',
    requester_id    INT REFERENCES helpdesk.users(id),
    assignee_id     INT REFERENCES helpdesk.users(id),
    escalated_to    INT REFERENCES helpdesk.users(id),
    sla_policy_id   INT REFERENCES helpdesk.sla_policies(id),
    sla_due_at      TIMESTAMPTZ,
    sla_first_response_due TIMESTAMPTZ,
    sla_breached    BOOLEAN DEFAULT false,
    source          TEXT DEFAULT 'web'
                    CHECK (source IN ('web', 'email', 'voice', 'chat', 'api', 'teams')),
    resolved_at     TIMESTAMPTZ,
    closed_at       TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_tickets_tenant ON helpdesk.tickets(tenant_id);
CREATE INDEX idx_tickets_status ON helpdesk.tickets(tenant_id, status);
CREATE INDEX idx_tickets_assignee ON helpdesk.tickets(assignee_id);
CREATE INDEX idx_tickets_sla ON helpdesk.tickets(sla_due_at) WHERE sla_breached = false AND status NOT IN ('resolved', 'closed');

-- ============================================================
-- TICKET COMMENTS / ACTIVITY
-- ============================================================
CREATE TABLE helpdesk.ticket_comments (
    id              SERIAL PRIMARY KEY,
    ticket_id       INT REFERENCES helpdesk.tickets(id) ON DELETE CASCADE,
    author_id       INT REFERENCES helpdesk.users(id),
    content         TEXT NOT NULL,
    is_internal     BOOLEAN DEFAULT false,
    is_ai_generated BOOLEAN DEFAULT false,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_comments_ticket ON helpdesk.ticket_comments(ticket_id);

-- ============================================================
-- NOTIFICATIONS
-- ============================================================
CREATE TABLE helpdesk.notifications (
    id              SERIAL PRIMARY KEY,
    tenant_id       INT REFERENCES helpdesk.tenants(id),
    ticket_id       INT REFERENCES helpdesk.tickets(id),
    channel         TEXT CHECK (channel IN ('teams_webhook', 'email', 'in_app', 'n8n')),
    recipient       TEXT,
    payload         JSONB,
    status          TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'failed')),
    error_message   TEXT,
    sent_at         TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_notifications_ticket ON helpdesk.notifications(ticket_id);
CREATE INDEX idx_notifications_status ON helpdesk.notifications(status) WHERE status = 'pending';

-- ============================================================
-- AI CONVERSATIONS
-- ============================================================
CREATE TABLE helpdesk.ai_conversations (
    id              SERIAL PRIMARY KEY,
    tenant_id       INT REFERENCES helpdesk.tenants(id),
    user_id         INT REFERENCES helpdesk.users(id),
    ticket_id       INT REFERENCES helpdesk.tickets(id),
    language        TEXT DEFAULT 'en',
    channel         TEXT CHECK (channel IN ('text', 'voice')),
    messages        JSONB DEFAULT '[]',
    modules_used    TEXT[] DEFAULT '{}',
    tokens_used     INT DEFAULT 0,
    escalated_to_ticket BOOLEAN DEFAULT false,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- CONNECTORS (links to external systems)
-- ============================================================
CREATE TABLE helpdesk.connectors (
    id              SERIAL PRIMARY KEY,
    tenant_id       INT REFERENCES helpdesk.tenants(id) ON DELETE CASCADE,
    connector_type  TEXT NOT NULL,
    name            TEXT,
    config_encrypted TEXT,
    is_active       BOOLEAN DEFAULT true,
    last_sync_at    TIMESTAMPTZ,
    last_error      TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- API KEYS (for n8n, external integrations)
-- ============================================================
CREATE TABLE helpdesk.api_keys (
    id              SERIAL PRIMARY KEY,
    tenant_id       INT REFERENCES helpdesk.tenants(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    key_hash        TEXT UNIQUE NOT NULL,
    key_prefix      TEXT NOT NULL,       -- First 8 chars for display (e.g., "hd_abc12...")
    permissions     TEXT[] DEFAULT '{tickets.read,tickets.write,ai.chat}',
    is_active       BOOLEAN DEFAULT true,
    last_used_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- TICKET NUMBER SEQUENCE
-- ============================================================
CREATE SEQUENCE helpdesk.ticket_number_seq START 1000;

-- ============================================================
-- SEED: Default knowledge modules
-- ============================================================
INSERT INTO helpdesk.knowledge_modules (slug, name, description, icon) VALUES
    ('toast', 'Toast POS', 'Toast platform guide — orders, menus, payments, kitchen ops', 'utensils'),
    ('solink', 'Solink', 'Solink video surveillance and loss prevention', 'video'),
    ('vsn', 'VSN', 'VSN system documentation', 'server'),
    ('powerbi', 'Power BI', 'Microsoft Power BI reporting and dashboards', 'bar-chart');
