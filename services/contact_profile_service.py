"""Contact profile service.

Passively builds a roster of contacts and their known locations from
ticket history. No manual data entry required — the roster fills itself
as tickets come in through any channel (portal, email, phone).

Primary consumers:
  - routes/tickets.py  → record_location on ticket create
  - services/atlas_service.py → get_location_suggestion for engage note
  - services/atlas_service.py → record_location on ticket close (learning)
"""

import logging

from models.db import fetch_one, fetch_all, insert_returning, execute

logger = logging.getLogger(__name__)

# Minimum tickets before we start making suggestions
MIN_TICKETS_FOR_SUGGESTION = 2

# Confidence threshold to auto-assign location silently
HIGH_CONFIDENCE = 85


def get_or_create_profile(
    tenant_id: int,
    user_id: int | None = None,
    email: str | None = None,
    phone: str | None = None,
    name: str | None = None,
) -> dict | None:
    """Return existing contact profile, creating one if needed.

    Lookup order: user_id first (most specific), then email.
    Returns None on any DB error — callers must handle gracefully.
    """
    if not tenant_id or (not user_id and not email):
        return None

    email_norm = email.lower().strip() if email else None

    try:
        # Try user_id first
        if user_id:
            profile = fetch_one(
                "SELECT * FROM helpdesk.contact_profiles WHERE tenant_id = %s AND user_id = %s",
                [tenant_id, user_id],
            )
            if profile:
                return dict(profile)

        # Try email
        if email_norm:
            profile = fetch_one(
                "SELECT * FROM helpdesk.contact_profiles WHERE tenant_id = %s AND LOWER(email) = %s",
                [tenant_id, email_norm],
            )
            if profile:
                # Backfill user_id if we now have it
                if user_id and not profile.get("user_id"):
                    execute(
                        "UPDATE helpdesk.contact_profiles SET user_id = %s, updated_at = NOW() WHERE id = %s",
                        [user_id, profile["id"]],
                    )
                return dict(profile)

        # Create new profile
        profile_id = insert_returning(
            """INSERT INTO helpdesk.contact_profiles
                   (tenant_id, user_id, email, phone, name)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT DO NOTHING
               RETURNING id""",
            [tenant_id, user_id, email_norm, phone, name],
        )
        if not profile_id:
            # Race condition — try fetch again
            if user_id:
                profile = fetch_one(
                    "SELECT * FROM helpdesk.contact_profiles WHERE tenant_id = %s AND user_id = %s",
                    [tenant_id, user_id],
                )
            else:
                profile = fetch_one(
                    "SELECT * FROM helpdesk.contact_profiles WHERE tenant_id = %s AND LOWER(email) = %s",
                    [tenant_id, email_norm],
                )
            return dict(profile) if profile else None

        return fetch_one(
            "SELECT * FROM helpdesk.contact_profiles WHERE id = %s", [profile_id]
        )

    except Exception as e:
        logger.warning("get_or_create_profile failed (tenant=%s user=%s): %s", tenant_id, user_id, e)
        return None


def record_location(profile_id: int, location_id: int, tenant_id: int) -> None:
    """Increment the visit count for a contact × location pair, then
    recompute primary_location_id and location_confidence on the profile.
    """
    if not profile_id or not location_id:
        return

    try:
        # Upsert location history row
        execute(
            """INSERT INTO helpdesk.contact_location_history
                   (tenant_id, contact_profile_id, location_id, ticket_count, last_seen_at)
               VALUES (%s, %s, %s, 1, NOW())
               ON CONFLICT (contact_profile_id, location_id)
               DO UPDATE SET
                   ticket_count = helpdesk.contact_location_history.ticket_count + 1,
                   last_seen_at = NOW()""",
            [tenant_id, profile_id, location_id],
        )

        # Recompute primary location + confidence
        history = fetch_all(
            """SELECT location_id, ticket_count
               FROM helpdesk.contact_location_history
               WHERE contact_profile_id = %s
               ORDER BY ticket_count DESC""",
            [profile_id],
        )
        if not history:
            return

        total = sum(row["ticket_count"] for row in history)
        top = history[0]
        confidence = int((top["ticket_count"] / total) * 100) if total > 0 else 0

        execute(
            """UPDATE helpdesk.contact_profiles
               SET primary_location_id = %s,
                   location_confidence = %s,
                   ticket_count        = %s,
                   updated_at          = NOW()
               WHERE id = %s""",
            [top["location_id"], confidence, total, profile_id],
        )

    except Exception as e:
        logger.warning("record_location failed (profile=%s location=%s): %s", profile_id, location_id, e)


def get_location_suggestion(profile_id: int) -> dict | None:
    """Return location suggestion data for a contact profile.

    Returns None if there is not enough history for a meaningful suggestion.

    Return shape:
    {
        "total_tickets": int,
        "confidence": int,          # 0-100
        "primary": {
            "location_id": int,
            "location_name": str,
            "ticket_count": int,
        },
        "all_locations": [          # sorted by ticket_count desc, max 5
            {"location_id": int, "location_name": str, "ticket_count": int},
            ...
        ],
    }
    """
    if not profile_id:
        return None

    try:
        profile = fetch_one(
            "SELECT * FROM helpdesk.contact_profiles WHERE id = %s", [profile_id]
        )
        if not profile or (profile.get("ticket_count") or 0) < MIN_TICKETS_FOR_SUGGESTION:
            return None

        rows = fetch_all(
            """SELECT clh.location_id, clh.ticket_count, loc.name as location_name
               FROM helpdesk.contact_location_history clh
               JOIN helpdesk.locations loc ON loc.id = clh.location_id
               WHERE clh.contact_profile_id = %s
               ORDER BY clh.ticket_count DESC
               LIMIT 5""",
            [profile_id],
        )
        if not rows:
            return None

        total = profile.get("ticket_count") or sum(r["ticket_count"] for r in rows)
        top = rows[0]

        return {
            "total_tickets": total,
            "confidence": profile.get("location_confidence") or 0,
            "primary": {
                "location_id": top["location_id"],
                "location_name": top["location_name"],
                "ticket_count": top["ticket_count"],
            },
            "all_locations": [
                {
                    "location_id": r["location_id"],
                    "location_name": r["location_name"],
                    "ticket_count": r["ticket_count"],
                }
                for r in rows
            ],
        }

    except Exception as e:
        logger.warning("get_location_suggestion failed (profile=%s): %s", profile_id, e)
        return None
