"""Triage service: simple workflow for chat intake.

Flow:
  1. Greet (custom per tenant)
  2. Check for location (ask if missing)
  3. One clarifying question if needed
  4. Route to AI / KB search

No AI calls — pure pattern matching against tenant data.
"""

import re
import logging

from models.db import fetch_all, fetch_one

logger = logging.getLogger(__name__)

# Words that signal frustration — respond with empathy
_FRUSTRATION_SIGNALS = {
    "frustrated", "frustrating", "angry", "furious", "ridiculous",
    "terrible", "awful", "horrible", "unacceptable", "broken",
    "nothing works", "still broken", "not working", "waste of time",
    "can't believe", "sick of", "tired of", "fed up",
}

_DEFAULT_GREETING = "Hi there! I'm here to help. What can I assist you with today?"


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------

def _get_tenant_info(tenant_id: int) -> dict:
    """Fetch tenant name, settings, and greeting."""
    row = fetch_one("SELECT name, settings FROM tenants WHERE id = %s", [tenant_id])
    if not row:
        return {"name": "Unknown", "settings": {}, "greeting": _DEFAULT_GREETING}
    settings = row.get("settings") or {}
    greeting = settings.get("chat_greeting") or _DEFAULT_GREETING
    return {"name": row["name"], "settings": settings, "greeting": greeting}


def _get_tenant_locations(tenant_id: int) -> list[dict]:
    """Fetch leaf-level locations (stores) for the tenant."""
    rows = fetch_all(
        """SELECT id, name, parent_id, level_label
           FROM locations
           WHERE tenant_id = %s AND is_active = true
           ORDER BY sort_order, name""",
        [tenant_id],
    )
    return rows or []


def _get_tenant_modules(tenant_id: int) -> list[dict]:
    """Get enabled module slugs and names."""
    rows = fetch_all(
        """SELECT km.id, km.slug, km.name
           FROM tenant_modules tm
           JOIN knowledge_modules km ON km.id = tm.module_id
           WHERE tm.tenant_id = %s AND km.is_active = true""",
        [tenant_id],
    )
    return rows or []


# ---------------------------------------------------------------------------
# Pattern matching
# ---------------------------------------------------------------------------

def _match_location(text: str, locations: list[dict]) -> dict | None:
    """Match a location name in user text. Prefers longest match."""
    text_lower = text.lower()
    best: dict | None = None
    best_len = 0

    for loc in locations:
        loc_name = loc["name"].lower()
        # Word-boundary first
        if re.search(r"\b" + re.escape(loc_name) + r"\b", text_lower):
            if len(loc_name) > best_len:
                best = loc
                best_len = len(loc_name)

    # Substring fallback
    if not best:
        for loc in locations:
            loc_name = loc["name"].lower()
            if loc_name in text_lower and len(loc_name) > best_len:
                best = loc
                best_len = len(loc_name)

    return best


def _infer_module(text: str, enabled_modules: list[dict]) -> dict | None:
    """If only one module enabled, return it. Otherwise check for name mention."""
    if len(enabled_modules) == 1:
        return enabled_modules[0]

    text_lower = text.lower()
    for m in enabled_modules:
        if m["slug"] in text_lower or m["name"].lower() in text_lower:
            return m

    return None


def _is_frustrated(text: str) -> bool:
    """Check if user seems frustrated."""
    text_lower = text.lower()
    return any(signal in text_lower for signal in _FRUSTRATION_SIGNALS)


# ---------------------------------------------------------------------------
# Triage result
# ---------------------------------------------------------------------------

class TriageResult:
    __slots__ = (
        "location", "module", "ready_for_ai",
        "response", "scoped_module_slug",
    )

    def __init__(self) -> None:
        self.location: dict | None = None
        self.module: dict | None = None
        self.ready_for_ai: bool = False
        self.response: str | None = None
        self.scoped_module_slug: str | None = None


# ---------------------------------------------------------------------------
# Main triage
# ---------------------------------------------------------------------------

def triage(messages: list[dict], tenant_id: int, language: str = "en") -> TriageResult:
    """Simple workflow triage.

    Turn 0 (no messages yet):  Greeting
    Turn 1 (first message):    Check for location, ask if missing
    Turn 2+:                   Route to AI with whatever we have
    """
    result = TriageResult()

    tenant = _get_tenant_info(tenant_id)
    locations = _get_tenant_locations(tenant_id)
    modules = _get_tenant_modules(tenant_id)
    es = language == "es"

    # Combine all user text for cumulative signal extraction
    all_user_text = " ".join(
        m["content"]
        for m in messages
        if m.get("role") == "user" and isinstance(m.get("content"), str)
    )
    user_turns = sum(1 for m in messages if m.get("role") == "user")

    # Always try to extract signals
    result.location = _match_location(all_user_text, locations)
    result.module = _infer_module(all_user_text, modules)
    if result.module:
        result.scoped_module_slug = result.module["slug"]

    frustrated = _is_frustrated(all_user_text)

    logger.info(
        "Triage: tenant=%s turns=%d loc=%s module=%s text=%r locs_count=%d",
        tenant_id, user_turns,
        result.location["name"] if result.location else None,
        result.module["slug"] if result.module else None,
        all_user_text[:80], len(locations),
    )

    # --- Turn 0: No user messages yet (shouldn't happen, but safety) ---
    if user_turns == 0:
        result.response = tenant["greeting"]
        return result

    # --- If we have location → ready for AI ---
    if result.location:
        result.ready_for_ai = True
        return result

    # --- After 2 turns, go to AI regardless ---
    if user_turns >= 2:
        result.ready_for_ai = True
        return result

    # --- Turn 1: Ask for location ---
    result.ready_for_ai = False

    # Build location options — show leaf nodes (stores)
    loc_names = [loc["name"] for loc in locations[:15]]

    empathy = ""
    if frustrated:
        empathy = ("Entiendo tu frustracion y estoy aqui para ayudarte. " if es
                   else "I completely understand your frustration, and I'm here to help. ")

    if loc_names:
        if es:
            result.response = (
                f"{empathy}Para poder asistirte mejor, "
                f"podrias indicarme en que ubicacion estas?\n\n"
                + "\n".join(f"  - {name}" for name in loc_names)
            )
        else:
            result.response = (
                f"{empathy}To help you as quickly as possible, "
                f"which location is this for?\n\n"
                + "\n".join(f"  - {name}" for name in loc_names)
            )
    else:
        # No locations configured — skip location step, go to AI
        result.ready_for_ai = True

    return result
