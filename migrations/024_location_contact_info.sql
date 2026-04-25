-- Migration 024: Location contact info + user-location assignments

-- Item 1: Add phone/email to locations table
ALTER TABLE helpdesk.locations
    ADD COLUMN IF NOT EXISTS phone VARCHAR(50),
    ADD COLUMN IF NOT EXISTS email VARCHAR(255);

-- Item 2: User <-> Location junction table
CREATE TABLE IF NOT EXISTS helpdesk.user_locations (
    id          SERIAL PRIMARY KEY,
    tenant_id   INTEGER NOT NULL,
    user_id     INTEGER NOT NULL REFERENCES helpdesk.users(id) ON DELETE CASCADE,
    location_id INTEGER NOT NULL REFERENCES helpdesk.locations(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, location_id)
);

CREATE INDEX IF NOT EXISTS idx_user_locations_tenant_user
    ON helpdesk.user_locations(tenant_id, user_id);

CREATE INDEX IF NOT EXISTS idx_user_locations_tenant_location
    ON helpdesk.user_locations(tenant_id, location_id);
