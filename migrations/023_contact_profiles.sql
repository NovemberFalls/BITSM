-- Migration 023: Contact profiles and location history
-- Builds a passive roster of contacts and their known locations
-- from ticket history. Used by Atlas to auto-assign/suggest locations.

CREATE TABLE IF NOT EXISTS helpdesk.contact_profiles (
    id                   SERIAL PRIMARY KEY,
    tenant_id            INTEGER NOT NULL REFERENCES helpdesk.tenants(id) ON DELETE CASCADE,
    user_id              INTEGER REFERENCES helpdesk.users(id) ON DELETE SET NULL,
    email                VARCHAR(255),
    phone                VARCHAR(50),
    name                 VARCHAR(255),
    primary_location_id  INTEGER REFERENCES helpdesk.locations(id) ON DELETE SET NULL,
    location_confidence  INTEGER DEFAULT 0 CHECK (location_confidence BETWEEN 0 AND 100),
    ticket_count         INTEGER DEFAULT 0,
    created_at           TIMESTAMPTZ DEFAULT NOW(),
    updated_at           TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (tenant_id, user_id),
    UNIQUE (tenant_id, email)
);

CREATE TABLE IF NOT EXISTS helpdesk.contact_location_history (
    id                   SERIAL PRIMARY KEY,
    tenant_id            INTEGER NOT NULL,
    contact_profile_id   INTEGER NOT NULL REFERENCES helpdesk.contact_profiles(id) ON DELETE CASCADE,
    location_id          INTEGER NOT NULL REFERENCES helpdesk.locations(id) ON DELETE CASCADE,
    ticket_count         INTEGER DEFAULT 1,
    last_seen_at         TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (contact_profile_id, location_id)
);

CREATE INDEX IF NOT EXISTS idx_contact_profiles_tenant
    ON helpdesk.contact_profiles(tenant_id);

CREATE INDEX IF NOT EXISTS idx_contact_profiles_user
    ON helpdesk.contact_profiles(tenant_id, user_id)
    WHERE user_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_contact_profiles_email
    ON helpdesk.contact_profiles(tenant_id, LOWER(email))
    WHERE email IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_contact_location_history_profile
    ON helpdesk.contact_location_history(contact_profile_id);
