-- 053_status_page.sql
-- Status Page: planned maintenance, outages, and known issues visible to portal users

SET search_path TO helpdesk, public;

CREATE TABLE IF NOT EXISTS status_incidents (
    id              SERIAL PRIMARY KEY,
    tenant_id       INT NOT NULL REFERENCES tenants(id),
    title           TEXT NOT NULL,
    body            TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'investigating'
                    CHECK (status IN ('scheduled','investigating','identified','monitoring','resolved')),
    severity        TEXT NOT NULL DEFAULT 'minor'
                    CHECK (severity IN ('minor','major','critical','maintenance')),
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    scheduled_end   TIMESTAMPTZ,          -- for scheduled maintenance windows
    resolved_at     TIMESTAMPTZ,
    created_by      INT REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_status_incidents_tenant ON status_incidents(tenant_id);
CREATE INDEX IF NOT EXISTS idx_status_incidents_status ON status_incidents(tenant_id, status);

CREATE TABLE IF NOT EXISTS status_incident_updates (
    id              SERIAL PRIMARY KEY,
    incident_id     INT NOT NULL REFERENCES status_incidents(id) ON DELETE CASCADE,
    body            TEXT NOT NULL,
    status          TEXT NOT NULL
                    CHECK (status IN ('scheduled','investigating','identified','monitoring','resolved')),
    created_by      INT REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_status_updates_incident ON status_incident_updates(incident_id);
