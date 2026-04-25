-- Migration 018: External email support for notification groups
-- Allows notification groups to contain external email addresses (not just registered users)

SET search_path = helpdesk, public;

-- Add email column and make user_id nullable
ALTER TABLE notification_group_members ADD COLUMN IF NOT EXISTS email TEXT;
ALTER TABLE notification_group_members ALTER COLUMN user_id DROP NOT NULL;

-- Ensure at least one of user_id or email is set
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'chk_member_has_contact'
  ) THEN
    ALTER TABLE notification_group_members
      ADD CONSTRAINT chk_member_has_contact CHECK (user_id IS NOT NULL OR email IS NOT NULL);
  END IF;
END $$;

-- Drop old unique constraint and create a new one that handles both cases
ALTER TABLE notification_group_members DROP CONSTRAINT IF EXISTS notification_group_members_group_id_user_id_key;

-- Unique index: prevent duplicate user or duplicate email per group
CREATE UNIQUE INDEX IF NOT EXISTS idx_notif_group_members_unique_user
  ON notification_group_members (group_id, user_id) WHERE user_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_notif_group_members_unique_email
  ON notification_group_members (group_id, email) WHERE email IS NOT NULL;
