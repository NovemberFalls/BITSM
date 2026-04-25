"""GDPR Right to Erasure (Article 17) — data anonymization service.

Anonymizes a user's PII across all tables while preserving referential
integrity and billing records.  This is a one-way, irreversible operation.

Tables addressed:
  - users: name, email, first_name, last_name, phone, avatar_url, preferences
  - ticket_comments: content replaced (user-authored only)
  - ai_conversations: messages cleared (user was participant)
  - atlas_engagements: summary cleared (linked via conversation)
  - phone_sessions: transcript + summary cleared, caller PII stripped
  - contact_profiles: PII cleared
  - contact_location_history: deleted (cascade from contact_profiles)
  - user_group_memberships: deleted
  - user_permission_overrides: deleted
  - user_locations: deleted
  - notifications: recipient/payload cleared where recipient matches user email
  - ticket_audit_queue: reviewed_by nullified
  - ticket_metrics: suggested_assignee_id nullified
  - tickets: completed_by nullified
"""

import logging

from models.db import cursor

logger = logging.getLogger(__name__)


def erase_user_data(user_id: int) -> dict:
    """Anonymize all PII for the given user.  Runs in a single transaction.

    Returns a summary dict with counts of affected rows per table.
    """
    anon_email = f"deleted_{user_id}@erased.local"
    anon_name = "Deleted User"
    summary = {}

    with cursor() as cur:
        # 0. Fetch original email before anonymizing (needed for notifications step)
        cur.execute("SELECT email FROM users WHERE id = %s", [user_id])
        row = cur.fetchone()
        original_email = row["email"] if row else None

        # 1. Anonymize the user record itself
        cur.execute(
            """UPDATE users
               SET name = %s,
                   email = %s,
                   first_name = %s,
                   last_name = NULL,
                   phone = NULL,
                   avatar_url = NULL,
                   preferences = '{}',
                   provider = NULL,
                   is_active = false,
                   invite_status = 'revoked'
               WHERE id = %s""",
            [anon_name, anon_email, anon_name, user_id],
        )
        summary["users"] = cur.rowcount

        # 2. Anonymize user-authored ticket comments
        #    Replace content but keep the row for ticket history continuity
        cur.execute(
            """UPDATE ticket_comments
               SET content = '[Content removed — account erased]'
               WHERE author_id = %s AND NOT is_ai_generated""",
            [user_id],
        )
        summary["ticket_comments"] = cur.rowcount

        # 3. Clear AI conversation messages where user was participant
        cur.execute(
            """UPDATE ai_conversations
               SET messages = '[]'
               WHERE user_id = %s""",
            [user_id],
        )
        summary["ai_conversations"] = cur.rowcount

        # 4. Clear atlas engagement summaries linked to user's conversations
        cur.execute(
            """UPDATE atlas_engagements
               SET summary = NULL
               WHERE conversation_id IN (
                   SELECT id FROM ai_conversations WHERE user_id = %s
               )""",
            [user_id],
        )
        summary["atlas_engagements"] = cur.rowcount

        # 5. Phone sessions — clear transcript/summary/caller PII
        #    Keep cost-relevant fields (duration_seconds, started_at, ended_at, status)
        cur.execute(
            """UPDATE phone_sessions
               SET transcript = '[]',
                   summary = NULL,
                   caller_phone = NULL,
                   caller_email = NULL
               WHERE caller_user_id = %s""",
            [user_id],
        )
        summary["phone_sessions"] = cur.rowcount

        # 6. Contact profiles — clear PII
        #    Delete location history first (FK constraint)
        cur.execute(
            """DELETE FROM contact_location_history
               WHERE contact_profile_id IN (
                   SELECT id FROM contact_profiles WHERE user_id = %s
               )""",
            [user_id],
        )
        summary["contact_location_history"] = cur.rowcount

        cur.execute(
            """UPDATE contact_profiles
               SET email = NULL,
                   phone = NULL,
                   name = %s,
                   primary_location_id = NULL,
                   location_confidence = 0,
                   ticket_count = 0
               WHERE user_id = %s""",
            [anon_name, user_id],
        )
        summary["contact_profiles"] = cur.rowcount

        # 7. RBAC — remove group memberships and permission overrides
        cur.execute(
            "DELETE FROM user_group_memberships WHERE user_id = %s",
            [user_id],
        )
        summary["user_group_memberships"] = cur.rowcount

        cur.execute(
            "DELETE FROM user_permission_overrides WHERE user_id = %s",
            [user_id],
        )
        summary["user_permission_overrides"] = cur.rowcount

        # 8. User-location assignments
        cur.execute(
            "DELETE FROM user_locations WHERE user_id = %s",
            [user_id],
        )
        summary["user_locations"] = cur.rowcount

        # 9. Notifications — clear recipient/payload where recipient matches user
        if original_email:
            cur.execute(
                """UPDATE notifications
                   SET recipient = NULL,
                       payload = '{}'
                   WHERE recipient = %s""",
                [original_email],
            )
            summary["notifications"] = cur.rowcount

        # 10. Nullify references in audit/metrics tables (keep records for analytics)
        cur.execute(
            "UPDATE ticket_audit_queue SET reviewed_by = NULL WHERE reviewed_by = %s",
            [user_id],
        )
        summary["ticket_audit_queue"] = cur.rowcount

        cur.execute(
            "UPDATE ticket_metrics SET suggested_assignee_id = NULL WHERE suggested_assignee_id = %s",
            [user_id],
        )
        summary["ticket_metrics"] = cur.rowcount

        # 11. Nullify tickets.completed_by where it references the user
        cur.execute(
            "UPDATE tickets SET completed_by = NULL WHERE completed_by = %s",
            [user_id],
        )
        summary["tickets_completed_by"] = cur.rowcount

    # Compliance audit trail: logged at INFO level for server log retention.
    # The anonymized user row (is_active=false, invite_status='revoked') itself
    # serves as the durable audit record of the erasure event.
    logger.info(
        "GDPR erasure completed for user_id=%s (original_email=%s): %s",
        user_id, original_email, summary,
    )
    return summary
