"""RAG service: agentic Claude tool-use loop for knowledge base Q&A."""

import json
import logging
import random
import time

from config import Config
from models.db import fetch_all, fetch_one
from services.llm_provider import log_call, _record_usage, get_anthropic_client

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8  # Max tool-use iterations per L1 conversation turn

# Whimsical status messages shown while waiting for AI response
_STATUS_PHRASES = [
    "Rummaging through the archives...",
    "Consulting the oracle...",
    "Deciphering ancient scrolls...",
    "Summoning the knowledge spirits...",
    "Brewing a potion of answers...",
    "Casting divination spells...",
    "Interrogating the data gnomes...",
    "Warming up the flux capacitor...",
    "Scouring the enchanted tomes...",
    "Activating turbo brain mode...",
    "Channeling the tech wizards...",
    "Untangling the knowledge web...",
    "Shaking the magic 8-ball...",
    "Deploying answer gremlins...",
    "Spinning up the wisdom engine...",
    "Querying the crystal database...",
    "Polishing the looking glass...",
    "Waking the library dragon...",
    "Charging the knowledge lasers...",
    "Firing up the answer forge...",
    "Consulting the elder algorithms...",
    "Traversing the info labyrinth...",
    "Calibrating the truth compass...",
    "Assembling the puzzle pieces...",
    "Feeding the hamsters powering this...",
    "Perusing the forbidden stacks...",
    "Triangulating the best answer...",
    "Running it through the brain blender...",
    "Engaging maximum nerd power...",
    "Sifting through the knowledge mines...",
    "Asking the rubber duck for help...",
    "Conjuring an incantation of insight...",
    "Peering into the abyss of docs...",
    "Booting up the answer machine...",
    "Dusting off the reference manuals...",
    "Crunching the knowledge crystals...",
    "Downloading wisdom from the cloud...",
    "Invoking the troubleshooting spirits...",
    "Unscrambling the answer matrix...",
    "Dispatching the search fairies...",
    "Winding up the clockwork brain...",
    "Plundering the knowledge vault...",
    "Tuning into the answer frequency...",
    "Igniting the cerebral thrusters...",
    "Rolling for intelligence check...",
    "Excavating the solution quarry...",
    "Powering up the insight reactor...",
    "Summoning a flock of helpful ravens...",
    "Alchemizing raw data into gold...",
]

# Tool definitions for Claude
TOOLS = [
    {
        "name": "kb_search",
        "description": (
            "Search the knowledge base using semantic similarity. Returns the most relevant "
            "documentation chunks with topic tags. Use this to find information before answering questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query — rephrase the user's question as a search query for best results",
                },
                "module": {
                    "type": "string",
                    "description": "Optional: filter to a specific knowledge module slug (e.g. 'toast', 'solink')",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional: pre-filter chunks to those matching ANY of these tags "
                        "before vector search. Narrows the search space for faster, more "
                        "accurate results. Use when the query targets a specific product or "
                        "feature (e.g. ['kds'], ['gift-cards', 'loyalty'], ['online-ordering'])."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of results to return (default 5, max 10)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "kb_lookup",
        "description": (
            "Retrieve the full content of a specific document by ID. Use this when you need "
            "more detail from a document found via kb_search."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "document_id": {
                    "type": "integer",
                    "description": "The document ID to retrieve",
                },
            },
            "required": ["document_id"],
        },
    },
    {
        "name": "list_articles",
        "description": (
            "Browse available knowledge base articles. Use this to discover what documentation "
            "is available or search by title."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "module": {
                    "type": "string",
                    "description": "Filter to a specific module slug",
                },
                "search": {
                    "type": "string",
                    "description": "Search term to filter article titles",
                },
            },
        },
    },
]


def _get_tenant_context(tenant_id: int) -> tuple[str, list[str]]:
    """Get tenant name and enabled module names."""
    tenant = fetch_one("SELECT name, settings FROM tenants WHERE id = %s", [tenant_id])
    tenant_name = tenant["name"] if tenant else "Unknown"

    modules = fetch_all(
        """SELECT km.name, km.slug
           FROM tenant_modules tm
           JOIN knowledge_modules km ON km.id = tm.module_id
           WHERE tm.tenant_id = %s AND km.is_active = true
             AND COALESCE(km.module_type, 'knowledge') = 'knowledge'""",
        [tenant_id],
    )
    module_names = [m["name"] for m in modules] if modules else ["No modules enabled"]
    return tenant_name, module_names


def _get_system_prompt(tenant_id: int, language: str) -> str:
    """Build system prompt for the direct Anthropic path (with tool-use loop)."""
    tenant_name, module_names = _get_tenant_context(tenant_id)

    lang_instruction = ""
    if language == "es":
        lang_instruction = "\n\nIMPORTANT: Respond in Spanish (Español). The user prefers Spanish."

    scope_restriction = (
        "SCOPE (NON-NEGOTIABLE):\n"
        f"You are a technical support assistant for {tenant_name} ONLY. "
        f"Your sole purpose is answering support questions covered by these modules: {', '.join(module_names)}.\n"
        "If a user asks ANYTHING outside this scope — homework, recipes, math, creative writing, "
        "general knowledge, coding help, opinions, or any non-support topic — respond with exactly:\n"
        f"\"I'm a support assistant for {tenant_name} and can only help with questions related to "
        f"{', '.join(module_names)}. For anything else, I'm not the right tool.\"\n"
        "Do NOT partially engage with off-topic requests. Do NOT hint at answers. Redirect immediately.\n\n"
    )

    return (
        f"You are a technical support AI assistant for {tenant_name}. "
        f"Knowledge modules available: {', '.join(module_names)}.\n\n"
        f"{scope_restriction}"
        "WORKFLOW:\n"
        "1. ALWAYS search the knowledge base before answering technical questions.\n"
        "2. When the query targets a specific product or feature, include the `tags` "
        "parameter in your kb_search call to narrow results (e.g. tags=['kds'], "
        "tags=['gift-cards', 'loyalty'], tags=['online-ordering', 'delivery']). "
        "This pre-filters chunks before vector search for faster, more focused results.\n"
        "3. You can search without tags first (broad), then refine with tags on a "
        "follow-up search if the initial results are noisy.\n"
        "4. Base your answers on the documentation you find. Cite the article title.\n"
        "5. If you cannot find relevant information, say so clearly — do NOT fabricate answers.\n"
        "6. Be concise. Use bullet points and numbered steps.\n"
        "7. Keep responses SHORT — agents are busy. Lead with the fix, not background info.\n"
        f"{lang_instruction}"
    )


def _build_dev_item_prompt(
    tenant_id: int,
    language: str,
    turn_count: int = 0,
    kb_context: list | None = None,
    conversation_history: list | None = None,
    ticket_context: dict | None = None,
) -> str:
    """Build a dev/coding-focused system prompt for work items (tasks, bugs, features)."""
    tenant_name, _ = _get_tenant_context(tenant_id)
    tc = ticket_context or {}

    lang_instruction = ""
    if language == "es":
        lang_instruction = "\nIMPORTANT: Respond in Spanish (Español).\n"

    item_type = tc.get("work_item_type_name") or tc.get("ticket_type", "task")
    wi_number = tc.get("work_item_number") or ""

    parts = [
        f"You are Atlas, a development assistant for {tenant_name}. "
        f"You are helping with a work item in the team's sprint/backlog system.\n",

        "MODE: Development & Engineering\n"
        "You are a knowledgeable coding partner. Be conversational, direct, and practical. "
        "Skip formalities — talk like a senior dev pairing with a colleague.\n\n",

        "WHAT YOU'RE GOOD AT:\n"
        "- Discussing implementation approaches, architecture, and trade-offs\n"
        "- Helping break down work items into subtasks\n"
        "- Suggesting acceptance criteria and definition of done\n"
        "- Estimating story point complexity (use team's scale if known)\n"
        "- Debugging strategies and root cause analysis\n"
        "- Code review guidance, refactoring suggestions\n"
        "- Recommending whether something is a story, epic, task, or bug\n"
        "- Keeping tags and categories consistent with team conventions\n\n",

        "WHAT YOU DON'T DO:\n"
        "- You don't triage or route work items to agents\n"
        "- You don't post auto-analysis or unsolicited internal notes\n"
        "- You don't escalate to L2 — this isn't a support flow\n"
        "- You don't need to search the KB unless the user asks about documentation\n\n",

        "STYLE:\n"
        "- Keep it short. Code snippets when helpful, not walls of text.\n"
        "- Use markdown: headers, bullets, code blocks.\n"
        "- If the user asks something vague, ask one clarifying question — don't guess.\n"
        "- Opinions are welcome when asked (\"I'd lean toward X because...\").\n\n",

        f"{lang_instruction}",
    ]

    # Inject work item context
    item_lines = [
        f"\nWORK ITEM CONTEXT:",
        f"  {item_type}: {wi_number} — {tc.get('subject', 'N/A')}",
        f"  Status: {tc.get('status', 'N/A')}  |  Priority: {tc.get('priority', 'N/A')}",
    ]
    if tc.get("sprint_name"):
        item_lines.append(f"  Sprint: {tc['sprint_name']}")
    if tc.get("story_points") is not None:
        item_lines.append(f"  Story Points: {tc['story_points']}")
    if tc.get("assignee_name"):
        item_lines.append(f"  Assignee: {tc['assignee_name']}")
    desc = tc.get("description", "")
    if desc:
        item_lines.append(f"  Description: {desc[:800]}")
    ac = tc.get("acceptance_criteria", "")
    if ac:
        item_lines.append(f"  Acceptance Criteria: {ac[:500]}")

    comments = tc.get("recent_comments") or []
    if comments:
        item_lines.append("  Recent discussion:")
        for cm in comments[:5]:
            prefix = "[Internal] " if cm.get("is_internal") else ""
            item_lines.append(
                f"    - {cm.get('author', 'Unknown')}: {prefix}{cm.get('content', '')[:200]}"
            )
    item_lines.append("")
    parts.append("\n".join(item_lines))

    # Inject KB context only if provided (pre-search may still run)
    if kb_context:
        kb_lines = []
        for r in kb_context[:3]:
            snippet = r.get("content", "")[:200]
            kb_lines.append(f"  [{r.get('title', 'Untitled')}]\n  {snippet}\n")
        parts.append("\nRELATED DOCS (if helpful):\n" + "\n".join(kb_lines))

    if conversation_history:
        history_lines = []
        for msg in conversation_history:
            role = "User" if msg.get("role") == "user" else "Atlas"
            history_lines.append(f"{role}: {msg.get('content', '')}")
        parts.append("\nCONVERSATION HISTORY:\n" + "\n".join(history_lines) + "\n")

    parts.append(f"\nCURRENT TURN: {turn_count}")

    return "\n".join(parts)


def _build_contextual_system_prompt(
    tenant_id: int,
    language: str,
    turn_count: int = 0,
    kb_context: list | None = None,
    locations: list | None = None,
    conversation_history: list | None = None,
    l2_analysis: str | None = None,
    persona: str = "agent",
    ticket_context: dict | None = None,
) -> str:
    """Build enriched system prompt with KB context, locations, and conversation history.

    Includes pre-search KB results, tenant locations, conversation
    history, and optional L2 analysis for post-escalation turns.
    """
    # Dev items (task/bug/feature) get a completely different prompt — coding-focused
    if ticket_context and ticket_context.get("ticket_type") in ("task", "bug", "feature"):
        return _build_dev_item_prompt(tenant_id, language, turn_count, kb_context,
                                      conversation_history, ticket_context)

    tenant_name, module_names = _get_tenant_context(tenant_id)

    lang_instruction = ""
    if language == "es":
        lang_instruction = "\nIMPORTANT: Respond in Spanish (Español). The user prefers Spanish.\n"

    is_agent = persona == "agent"

    # Base prompt changes depending on whether we have L2 analysis
    if l2_analysis:
        parts = [
            f"You are a technical support AI assistant for {tenant_name}. "
            f"Knowledge modules: {', '.join(module_names)}.\n",
            "SCOPE (NON-NEGOTIABLE): You only answer support questions for these modules. "
            "Decline anything off-topic immediately and redirect.\n\n",
            "A senior colleague has analyzed this issue in depth (see COLLEAGUE ANALYSIS below). "
            "Your job is to walk the user through the solution ONE STEP AT A TIME.\n\n"
            "WORKFLOW:\n"
            "1. Present the NEXT logical step from the colleague's analysis.\n"
            "2. Wait for the user to confirm they tried it before moving on.\n"
            "3. If a step resolves it, confirm and wrap up.\n"
            "4. If ALL steps from the analysis are exhausted and the issue persists, "
            "inform the user: \"I've exhausted my troubleshooting options. Someone from "
            "our team will reach out to you via email to assist further.\"\n"
            "5. Keep responses SHORT — one step at a time, not the whole plan.\n"
            "6. Stay calm and professional even if the user is frustrated.\n",
            f"{lang_instruction}",
        ]
    else:
        # Common sections shared by both personas
        tool_budget = (
            "TOOL BUDGET (CRITICAL — read this first):\n"
            "You have up to 7 tool calls. The system KILLS you at 8 with no output.\n"
            "Be thorough — search multiple angles, look up full articles, cross-reference. "
            "But you MUST deliver your answer by call 7. Do NOT use all 8 on tools.\n"
            "- PRE-SEARCH results are already included below. Start there.\n"
            "- If pre-search answers the question → respond immediately, no tools needed.\n"
            "- If you need more detail → KB Lookup on the best results.\n"
            "- If pre-search missed → KB Search with better queries, then look up hits.\n"
            "- Count your calls. When you hit 6, your NEXT action MUST be your final answer.\n"
            "- If the question is vague, ASK a clarifying question instead of searching blindly.\n\n"
        )

        workflow = (
            "WORKFLOW:\n"
            "1. TURN 1: Review the pre-searched KB results below. Provide troubleshooting steps "
            "based on what you found. Ask at most ONE clarifying question if needed "
            "(e.g., printer type, which store).\n"
            "2. TURN 2+: If the user confirms steps didn't work, try different approaches from "
            "the KB. Be concise — bullet points and numbered steps. Cite the article title.\n"
            "3. NEVER ask more than 2 clarifying questions total. After turn 3, answer with what you have.\n"
            "4. If the KB has no relevant info, say so — do NOT fabricate answers.\n"
            "5. Keep responses SHORT — lead with the fix, not background info.\n\n"
        )

        escalation = (
            "ESCALATION RULES:\n"
            "- If the user reports your steps DIDN'T WORK or the issue PERSISTS after you've "
            "given substantive troubleshooting, append the exact marker <<ESCALATE>> at the "
            "very end of your message (after your visible response to the user). This signals "
            "the system to consult a senior specialist.\n"
            "- Before escalating, acknowledge the user's frustration and let them know you're "
            "getting additional help: \"Let me consult with a colleague who may have deeper "
            "insight into this. One moment please.\"\n"
            "- Do NOT escalate on the first turn — always try your own troubleshooting first.\n"
            "- Do NOT escalate just because the user is frustrated — de-escalate first, "
            "stay calm, and focus on resolving the issue.\n"
            "- Only escalate when your recommended steps have genuinely failed.\n\n"
        )

        if is_agent:
            # Agent persona: reference vendor names, prepare agent for vendor contact
            resolution = (
                "RESOLUTION & CLOSURE:\n"
                "- When troubleshooting is exhausted and the issue requires vendor intervention, "
                "prepare the agent to contact the vendor. Include:\n"
                "  a) The vendor name and recommended contact method.\n"
                "  b) A summary of what was tried and what failed.\n"
                "  c) Specific article references the vendor support team will recognize.\n"
                "  d) Example: \"Contact Toast Support. Mention you tried [steps] per article "
                "'[title]'. The issue points to [diagnosis].\"\n"
                "- When the issue is a clear hardware failure or requires replacement, state "
                "that directly: \"This is a hardware failure — the printer needs replacement. "
                "Contact [vendor] to arrange it.\"\n"
                "- The agent's job is to act on your diagnosis — give them everything they need "
                "to resolve it quickly, whether that's more troubleshooting or a vendor call.\n"
            )
        else:
            # End-user persona: never expose vendor names, customer-friendly tone
            resolution = (
                "AUDIENCE: You are speaking DIRECTLY to an end-user (employee/customer), "
                "NOT a support agent. Adjust your tone accordingly:\n"
                "- Be warm, patient, and reassuring.\n"
                "- Use plain language — avoid technical jargon unless it helps the user.\n"
                "- NEVER reference vendor or product names (e.g., do NOT say 'Toast', "
                "'Solink', 'VSN', etc.). The user doesn't need to know which vendor "
                "is behind the system.\n"
                "- Instead of telling them to 'contact the vendor', say: \"I'll make sure "
                "our team follows up on this\" or \"Someone from our team will reach out "
                "to help with this.\"\n"
                "- When the issue requires hands-on support or hardware replacement, say: "
                "\"This looks like it needs hands-on attention. Our team will follow up "
                "with you to get this resolved.\"\n"
                "- Focus on what the USER can try, not what an agent should do.\n"
            )

        scope_restriction = (
            "SCOPE (NON-NEGOTIABLE):\n"
            f"You are a technical support assistant for {tenant_name} ONLY. "
            f"Your sole purpose is answering support questions covered by these modules: {', '.join(module_names)}.\n"
            "If a user asks ANYTHING outside this scope — homework, recipes, math, creative writing, "
            "general knowledge, coding help, opinions, or any non-support topic — respond with exactly:\n"
            f"\"I'm a support assistant for {tenant_name} and can only help with questions related to "
            f"{', '.join(module_names)}. For anything else, I'm not the right tool.\"\n"
            "Do NOT partially engage with off-topic requests. Do NOT hint at answers. Redirect immediately.\n\n"
        )

        parts = [
            f"You are a technical support AI assistant for {tenant_name}. "
            f"Knowledge modules: {', '.join(module_names)}.\n",
            scope_restriction,
            tool_budget,
            workflow,
            escalation,
            resolution,
            f"{lang_instruction}",
        ]

    # Inject tenant locations
    if locations:
        loc_lines = []
        for loc in locations[:30]:
            label = f" ({loc['level_label']})" if loc.get("level_label") else ""
            loc_lines.append(f"  - {loc['name']}{label}")
        parts.append("\nTENANT LOCATIONS (match user mentions to these):\n" + "\n".join(loc_lines) + "\n")

    # Inject pre-search KB results
    if kb_context:
        kb_lines = []
        for r in kb_context[:5]:
            snippet = r.get("content", "")[:300]
            sim = r.get("similarity", 0)
            kb_lines.append(f"  [{r.get('title', 'Untitled')}] (relevance: {sim})\n  {snippet}\n")
        parts.append("\nPRE-SEARCH KB RESULTS:\n" + "\n".join(kb_lines))

    # Inject conversation history for full context
    if conversation_history:
        history_lines = []
        for msg in conversation_history:
            role = "User" if msg.get("role") == "user" else "Assistant"
            history_lines.append(f"{role}: {msg.get('content', '')}")
        parts.append("\nCONVERSATION HISTORY:\n" + "\n".join(history_lines) + "\n")

    # Inject L2 colleague analysis (post-escalation turns)
    if l2_analysis:
        parts.append(
            "\nCOLLEAGUE ANALYSIS (from senior specialist — walk user through this step by step):\n"
            + l2_analysis + "\n"
        )

    # Inject ticket context (when chat is scoped to a ticket)
    if ticket_context:
        tc = ticket_context
        ticket_lines = [
            "\nTICKET CONTEXT (you are assisting with this specific ticket):",
            "IMPORTANT: You already know the issue from this ticket. Do NOT ask the user to "
            "describe the problem again — use the subject, description, and location below. "
            "When the user says 'analyze', 'thoughts?', or similar, analyze THIS ticket. "
            "Search the KB using the ticket subject/description, not the user's chat message.",
            f"  Subject: {tc.get('subject', 'N/A')}",
            f"  Status: {tc.get('status', 'N/A')}  |  Priority: {tc.get('priority', 'N/A')}",
        ]
        desc = tc.get("description", "")
        if desc:
            ticket_lines.append(f"  Description: {desc[:500]}")
        if tc.get("requester_name"):
            ticket_lines.append(f"  Requester: {tc['requester_name']}")
        if tc.get("assignee_name"):
            ticket_lines.append(f"  Assigned to: {tc['assignee_name']}")
        if tc.get("location_name"):
            ticket_lines.append(f"  Location: {tc['location_name']}")
        if tc.get("category_name"):
            ticket_lines.append(f"  Category: {tc['category_name']}")
        comments = tc.get("recent_comments") or []
        if comments:
            ticket_lines.append("  Recent activity (newest first):")
            for cm in comments[:5]:
                prefix = "[Internal] " if cm.get("is_internal") else ""
                ticket_lines.append(
                    f"    - {cm.get('author', 'Unknown')}: {prefix}{cm.get('content', '')[:200]}"
                )
        # Inject custom fields context
        custom_fields = tc.get("custom_fields") or []
        unfilled_to_close = tc.get("unfilled_required_to_close") or []
        if custom_fields:
            ticket_lines.append("  Custom Fields:")
            for cf in custom_fields:
                val = cf.get("value")
                val_str = str(val) if val is not None else "(not set)"
                markers = []
                if cf.get("required_to_create"):
                    markers.append("REQUIRED TO CREATE")
                if cf.get("required_to_close"):
                    markers.append("REQUIRED TO CLOSE")
                marker_str = f" [{', '.join(markers)}]" if markers else ""
                key_hint = f" (field_key: {cf['key']})" if cf.get("key") else ""
                ticket_lines.append(f"    - {cf['name']}{marker_str}{key_hint}: {val_str}")
        if custom_fields:
            ticket_lines.append("")
            ticket_lines.append(
                "Note: Custom fields are managed by the tenant — if the user asks about setting a custom field, "
                "let them know they can update it directly in the ticket detail sidebar under Custom Fields."
            )
        if unfilled_to_close:
            ticket_lines.append(
                "CRITICAL — REQUIRED TO CLOSE: The following fields MUST be filled before this ticket can be "
                "closed or auto-resolved: "
                + ", ".join(f'"{f}"' for f in unfilled_to_close)
                + ". You MUST proactively collect this information through conversation BEFORE attempting to resolve "
                "the ticket. If the user tries to close without these filled, remind them these are required. "
                "Do NOT send the [RESOLVED] signal until all required-to-close fields are confirmed filled. "
                "Direct the user to update the fields in the ticket sidebar under Custom Fields."
            )

        ticket_lines.append(
            "RESOLUTION SIGNAL: When the user clearly confirms the issue is resolved "
            "(e.g., 'that worked!', 'it\\'s fixed now', 'problem solved', 'thanks, all good'), "
            "append exactly [RESOLVED] on a new line at the very end of your response "
            "(after all visible text, including your sign-off). This is a hidden system signal "
            "— do NOT mention it to the user. "
            "Only use [RESOLVED] when the user definitively confirms resolution — NOT for "
            "general thanks, greetings, or partial success."
        )
        ticket_lines.append("")
        parts.append("\n".join(ticket_lines))

    parts.append(f"\nCURRENT TURN: {turn_count}")

    return "\n".join(parts)


def _get_enabled_module_ids(tenant_id: int) -> list[int]:
    """Get module IDs enabled for a tenant."""
    rows = fetch_all(
        "SELECT module_id FROM tenant_modules WHERE tenant_id = %s",
        [tenant_id],
    )
    return [r["module_id"] for r in rows]


def _tool_set_custom_field(field_key: str, value, tenant_id: int,
                           ticket_id: int | None = None) -> str:
    """Set a custom field value on the current ticket."""
    if not ticket_id:
        return json.dumps({"error": "No ticket context — cannot set custom field"})

    # Resolve field definition
    field_def = fetch_one(
        "SELECT id, name, field_type FROM custom_field_definitions "
        "WHERE tenant_id = %s AND field_key = %s AND is_active = true",
        [tenant_id, field_key],
    )
    if not field_def:
        return json.dumps({"error": f"Custom field '{field_key}' not found for this tenant"})

    # Upsert the value
    execute(
        """INSERT INTO ticket_custom_field_values (ticket_id, field_id, value, set_by, set_at)
           VALUES (%s, %s, %s::jsonb, NULL, now())
           ON CONFLICT (ticket_id, field_id)
           DO UPDATE SET value = EXCLUDED.value, set_by = EXCLUDED.set_by, set_at = now()""",
        [ticket_id, field_def["id"], json.dumps(value)],
    )

    logger.info("Atlas set custom field %s=%s on ticket %s", field_key, value, ticket_id)

    # Re-check unfilled required-to-close fields so Atlas knows when all are collected
    still_unfilled = []
    try:
        ticket_row = fetch_one(
            "SELECT problem_category_id, ticket_type FROM tickets WHERE id = %s",
            [ticket_id],
        )
        if ticket_row:
            cat_id = ticket_row.get("problem_category_id")
            t_type = ticket_row.get("ticket_type") or "support"
            if cat_id:
                req_defs = fetch_all(
                    """WITH RECURSIVE cat_ancestors AS (
                           SELECT id FROM problem_categories WHERE id = %s
                           UNION ALL
                           SELECT pc.parent_id
                           FROM problem_categories pc
                           JOIN cat_ancestors ca ON pc.id = ca.id
                           WHERE pc.parent_id IS NOT NULL
                       )
                       SELECT id, name, field_key FROM custom_field_definitions
                       WHERE tenant_id = %s AND is_active = true AND is_required_to_close = true
                         AND (category_id IN (SELECT id FROM cat_ancestors)
                              OR (category_id IS NULL AND %s = ANY(applies_to)))""",
                    [cat_id, tenant_id, t_type],
                )
            else:
                req_defs = fetch_all(
                    """SELECT id, name, field_key FROM custom_field_definitions
                       WHERE tenant_id = %s AND is_active = true AND is_required_to_close = true
                         AND category_id IS NULL AND %s = ANY(applies_to)""",
                    [tenant_id, t_type],
                )
            filled_ids = {
                r["field_id"]
                for r in fetch_all(
                    "SELECT field_id FROM ticket_custom_field_values WHERE ticket_id = %s",
                    [ticket_id],
                )
            }
            still_unfilled = [d["name"] for d in req_defs if d["id"] not in filled_ids]
    except Exception as e:
        logger.warning("Could not check unfilled required fields for ticket %s: %s", ticket_id, e)

    result = {
        "ok": True,
        "field_name": field_def["name"],
        "field_key": field_key,
        "value": value,
        "message": f"Successfully set '{field_def['name']}' to {value}",
    }
    if still_unfilled:
        result["remaining_required_to_close"] = still_unfilled
        result["message"] += f". Still need: {', '.join(still_unfilled)}"
    else:
        result["all_required_filled"] = True
        result["message"] += ". All required-to-close fields are now filled."

    return json.dumps(result)


def _execute_tool(tool_name: str, tool_input: dict, tenant_id: int,
                   ticket_id: int | None = None) -> str:
    """Execute a tool call and return the result as a string."""
    try:
        if tool_name == "kb_search":
            return _tool_kb_search(
                query=tool_input["query"],
                module=tool_input.get("module"),
                tags=tool_input.get("tags"),
                limit=min(tool_input.get("limit", 5), 10),
                tenant_id=tenant_id,
            )
        elif tool_name == "kb_lookup":
            return _tool_kb_lookup(tool_input["document_id"], tenant_id)
        elif tool_name == "list_articles":
            return _tool_list_articles(
                module=tool_input.get("module"),
                search=tool_input.get("search"),
                tenant_id=tenant_id,
            )
        else:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
    except Exception as e:
        logger.error("Tool execution error (%s): %s", tool_name, e)
        return json.dumps({"error": str(e)})


# ── Feedback-boosted ranking ─────────────────────────────────

def _apply_effectiveness_boost(results: list[dict]) -> list[dict]:
    """Re-rank search results using article effectiveness scores from user feedback.

    Score formula: final_score = similarity * (0.7 + 0.3 * effectiveness)
    - No feedback (effectiveness is None): treated as 0.5 (neutral)
    - >80% positive: boost up to 0.94
    - >60% negative: penalty down to 0.82
    """
    if not results:
        return results

    doc_ids = list({r["document_id"] for r in results if r.get("document_id")})
    if not doc_ids:
        return results

    # Batch-fetch effectiveness scores
    scores = {}
    try:
        rows = fetch_all(
            "SELECT id, effectiveness_score FROM documents WHERE id = ANY(%s)",
            [doc_ids],
        )
        for row in rows:
            if row.get("effectiveness_score") is not None:
                scores[row["id"]] = row["effectiveness_score"]
    except Exception:
        pass  # If column doesn't exist yet, skip gracefully

    # Apply boost and re-sort
    for r in results:
        effectiveness = scores.get(r.get("document_id"), 0.5)  # default neutral
        similarity = r.get("similarity", 0)
        r["effectiveness_score"] = round(effectiveness, 3)
        r["boosted_score"] = round(similarity * (0.7 + 0.3 * effectiveness), 4)

    results.sort(key=lambda r: r.get("boosted_score", 0), reverse=True)
    return results


def recalculate_effectiveness(document_id: int):
    """Recalculate and store effectiveness_score for a document based on feedback.

    Called after thumbs up/down or article rating submission.
    """
    from models.db import execute
    try:
        row = fetch_one(
            """SELECT
                 count(*) FILTER (WHERE user_helpful = true) as positive,
                 count(*) FILTER (WHERE user_helpful = false) as negative,
                 count(*) as total
               FROM article_recommendations
               WHERE document_id = %s AND user_helpful IS NOT NULL""",
            [document_id],
        )
        if not row or row["total"] == 0:
            return

        total = row["positive"] + row["negative"]
        if total == 0:
            return

        score = row["positive"] / total
        execute(
            "UPDATE documents SET effectiveness_score = %s, rating_count = %s WHERE id = %s",
            [round(score, 4), total, document_id],
        )

        # Auto-flag low-effectiveness articles for review
        if score < 0.3 and total >= 5:
            existing = fetch_one(
                "SELECT id FROM knowledge_gaps WHERE topic = %s AND status = 'detected'",
                [f"Low effectiveness: document {document_id}"],
            )
            if not existing:
                from models.db import insert_returning
                doc = fetch_one("SELECT title FROM documents WHERE id = %s", [document_id])
                title = doc["title"] if doc else f"Document #{document_id}"
                insert_returning(
                    """INSERT INTO knowledge_gaps (tenant_id, topic, ticket_count, suggested_title, status)
                       VALUES (NULL, %s, %s, %s, 'detected') RETURNING id""",
                    [f"Low effectiveness: {title}", total, f"Review and update: {title}"],
                )
                logger.info("Flagged low-effectiveness article: %s (score=%.2f, ratings=%d)", title, score, total)

    except Exception as e:
        logger.warning("Effectiveness recalculation failed for doc %d: %s", document_id, e)


def _tool_kb_search(
    query: str,
    module: str | None,
    limit: int,
    tenant_id: int,
    tags: list[str] | None = None,
) -> str:
    """Vector similarity search against document_chunks.

    When ``tags`` is provided, a GIN-indexed array overlap filter
    (``dc.tags && ARRAY[...]``) narrows the search space *before* the
    cosine distance scan — Phase 2 tag pre-filtering.
    """
    from services.embedding_service import embed_single_with_usage

    module_ids = _get_enabled_module_ids(tenant_id)

    # If specific module requested, filter further
    if module:
        module_row = fetch_one("SELECT id FROM knowledge_modules WHERE slug = %s", [module])
        if module_row and module_row["id"] in module_ids:
            module_ids = [module_row["id"]]

    query_embedding, embed_tokens = embed_single_with_usage(query, tenant_id=tenant_id)

    # Record embedding token usage for billing
    if embed_tokens and tenant_id:
        try:
            from config import Config as _Cfg
            _record_usage(tenant_id, None, _Cfg.EMBEDDING_PROVIDER, _Cfg.EMBEDDING_MODEL,
                          "rag.kb_search", embed_tokens, 0)
            from services import billing_service
            billing_service.record_usage(tenant_id, _Cfg.EMBEDDING_MODEL, "rag.kb_search", embed_tokens, 0)
        except Exception:
            pass

    # Phase 2: Tag-based pre-filtering via GIN index on dc.tags
    tag_clause = ""
    # Scope: tenant's enabled modules OR tenant's own articles (module_id IS NULL)
    params: list = [str(query_embedding), module_ids, tenant_id]
    if tags:
        # Normalise: lowercase, strip whitespace, dedupe
        clean_tags = list({t.strip().lower() for t in tags if t and t.strip()})
        if clean_tags:
            tag_clause = "AND dc.tags && %s::text[]"
            params.append(clean_tags)

    results = fetch_all(
        f"""SELECT dc.id, dc.document_id, dc.content, dc.token_count, dc.metadata,
                  dc.tags,
                  d.title, d.source_url,
                  1 - (dc.embedding <=> %s::vector) as similarity
           FROM document_chunks dc
           JOIN documents d ON d.id = dc.document_id AND d.is_published = true
           WHERE (dc.module_id = ANY(%s)
                  OR (dc.module_id IS NULL AND d.tenant_id = %s))
                 {tag_clause}
           ORDER BY dc.embedding <=> %s::vector
           LIMIT %s""",
        params + [str(query_embedding), limit],
    )

    formatted = []
    for r in results:
        formatted.append({
            "document_id": r["document_id"],
            "title": r["title"],
            "source_url": r["source_url"],
            "section": (r.get("metadata") or {}).get("section"),
            "module": (r.get("metadata") or {}).get("module"),
            "tags": r.get("tags") or (r.get("metadata") or {}).get("tags", []),
            "similarity": round(r.get("similarity", 0), 4),
            "content": r["content"],
        })

    # Apply feedback-boosted ranking
    formatted = _apply_effectiveness_boost(formatted)

    return json.dumps({"results": formatted, "count": len(formatted)})


def _tool_kb_lookup(document_id: int, tenant_id: int) -> str:
    """Retrieve full document content."""
    module_ids = _get_enabled_module_ids(tenant_id)

    doc = fetch_one(
        """SELECT d.id, d.title, d.content, d.source_url,
                  COALESCE(km.name, tc.name) as module_name,
                  COALESCE(km.slug, 'tenant') as module_slug
           FROM documents d
           LEFT JOIN knowledge_modules km ON km.id = d.module_id
           LEFT JOIN tenant_collections tc ON tc.id = d.tenant_collection_id
           WHERE d.id = %s AND (d.module_id = ANY(%s) OR d.module_id IS NULL)""",
        [document_id, module_ids],
    )

    if not doc:
        return json.dumps({"error": "Document not found or not accessible"})

    return json.dumps({
        "id": doc["id"],
        "title": doc["title"],
        "module": doc.get("module_slug"),
        "source_url": doc["source_url"],
        "content": doc["content"][:8000],  # Cap at ~8000 chars to stay within context
    })


def _tool_list_articles(module: str | None, search: str | None, tenant_id: int) -> str:
    """List available articles, optionally filtered."""
    module_ids = _get_enabled_module_ids(tenant_id)
    if not module_ids:
        return json.dumps({"articles": [], "message": "No modules enabled"})

    conditions = ["(d.module_id = ANY(%s) OR d.module_id IS NULL)"]
    params: list = [module_ids]

    if module:
        module_row = fetch_one("SELECT id FROM knowledge_modules WHERE slug = %s", [module])
        if module_row:
            conditions.append("d.module_id = %s")
            params.append(module_row["id"])

    if search:
        conditions.append("d.title ILIKE %s")
        params.append(f"%{search}%")

    where = " AND ".join(conditions)
    articles = fetch_all(
        f"""SELECT d.id, d.title, d.source_url,
                   COALESCE(km.slug, 'tenant') as module_slug,
                   COALESCE(km.name, tc.name) as module_name,
                   length(d.content) as content_length
            FROM documents d
            LEFT JOIN knowledge_modules km ON km.id = d.module_id
            LEFT JOIN tenant_collections tc ON tc.id = d.tenant_collection_id
            WHERE {where}
            ORDER BY d.title
            LIMIT 20""",
        params,
    )

    return json.dumps({
        "articles": [
            {
                "id": a["id"],
                "title": a["title"],
                "module": a.get("module_slug"),
                "source_url": a["source_url"],
                "content_length": a["content_length"],
            }
            for a in articles
        ],
        "count": len(articles),
    })


def _extract_sources(messages: list[dict]) -> list[dict]:
    """Extract source citations from tool results in the conversation."""
    sources = {}
    for msg in messages:
        if msg.get("role") == "user" and isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if block.get("type") == "tool_result":
                    try:
                        data = json.loads(block.get("content", "{}"))
                        for result in data.get("results", []):
                            doc_id = result.get("document_id")
                            if doc_id and doc_id not in sources:
                                sources[doc_id] = {
                                    "document_id": doc_id,
                                    "title": result.get("title", ""),
                                    "url": result.get("source_url", ""),
                                    "module": result.get("module", ""),
                                }
                    except (json.JSONDecodeError, AttributeError):
                        pass
    return list(sources.values())


def generate_response(tenant_id: int, messages: list[dict], language: str = "en",
                      system_override: str | None = None,
                      model_override: str | None = None,
                      caller: str = "l1_chat",
                      ticket_id: int | None = None,
                      skip_tools: bool = False) -> dict:
    """Non-streaming RAG response. Runs tool-use loop until Claude produces final text."""
    client = get_anthropic_client(tenant_id)
    system = system_override or _get_system_prompt(tenant_id, language)
    model = model_override or Config.AI_MODEL_CHAT

    # Convert simple messages to Anthropic format
    api_messages = _to_api_messages(messages)
    total_tokens = 0
    total_tokens_in = 0
    total_tokens_out = 0
    modules_used = set()
    t0 = time.perf_counter()

    for _round in range(MAX_TOOL_ROUNDS):
        call_kwargs: dict = {
            "model": model,
            "max_tokens": 4096,
            "system": system,
            "messages": api_messages,
        }
        if not skip_tools:
            call_kwargs["tools"] = TOOLS
        response = client.messages.create(**call_kwargs)

        total_tokens += (response.usage.input_tokens + response.usage.output_tokens)
        total_tokens_in += response.usage.input_tokens
        total_tokens_out += response.usage.output_tokens

        # Check if Claude wants to use tools
        if response.stop_reason == "tool_use":
            # Append assistant message with tool_use blocks
            api_messages.append({"role": "assistant", "content": response.content})

            # Execute each tool
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result_text = _execute_tool(block.name, block.input, tenant_id, ticket_id=ticket_id)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_text,
                    })
                    # Track modules used
                    try:
                        data = json.loads(result_text)
                        for r in data.get("results", []):
                            if r.get("module"):
                                modules_used.add(r["module"])
                    except (json.JSONDecodeError, AttributeError):
                        pass

            # Inject round budget so the LLM knows when to wrap up
            remaining = MAX_TOOL_ROUNDS - (_round + 1)
            budget_msg = f"[Tool budget: {_round + 1}/{MAX_TOOL_ROUNDS} used, {remaining} remaining."
            if remaining <= 1:
                budget_msg += " LAST ROUND — respond with your answer NOW."
            elif remaining <= 2:
                budget_msg += " You MUST deliver your final answer on your next response."
            budget_msg += "]"
            tool_results.append({"type": "text", "text": budget_msg})

            api_messages.append({"role": "user", "content": tool_results})
        else:
            # Claude produced final text
            answer = ""
            for block in response.content:
                if hasattr(block, "text"):
                    answer += block.text

            sources = _extract_sources(api_messages)

            latency = (time.perf_counter() - t0) * 1000
            log_call(caller, tenant_id, ticket_id, "anthropic",
                     model, latency, total_tokens_in,
                     total_tokens_out, True)
            _record_usage(tenant_id, ticket_id, "anthropic", model, caller,
                          total_tokens_in, total_tokens_out)
            return {
                "answer": answer,
                "sources": sources,
                "modules_used": list(modules_used),
                "tokens": total_tokens,
            }

    # Safety: max rounds exceeded — return empty with fallback flag
    # NEVER return the raw error message; callers check 'fallback' flag
    latency = (time.perf_counter() - t0) * 1000
    log_call(caller, tenant_id, ticket_id, "anthropic",
             model, latency, total_tokens_in,
             total_tokens_out, True)
    _record_usage(tenant_id, ticket_id, "anthropic", model, caller,
                  total_tokens_in, total_tokens_out)
    logger.warning("RAG max rounds (%d) exceeded for tenant %s — returning fallback", MAX_TOOL_ROUNDS, tenant_id)
    return {
        "answer": "",
        "sources": _extract_sources(api_messages),
        "modules_used": list(modules_used),
        "tokens": total_tokens,
        "fallback": True,
    }


def generate_response_stream(tenant_id: int, messages: list[dict], language: str = "en",
                             system_override: str | None = None,
                             caller: str = "l1_chat",
                             ticket_id: int | None = None):
    """Streaming RAG response generator. Yields SSE event dicts.

    Event types:
        status  — { type: 'status', content: 'Searching knowledge base...' }
        text    — { type: 'text', content: '...' }
        sources — { type: 'sources', sources: [...], modules_used: [...] }
        done    — { type: 'done', tokens: N }
    """
    client = get_anthropic_client(tenant_id)
    system = system_override or _get_system_prompt(tenant_id, language)
    api_messages = _to_api_messages(messages)
    total_tokens = 0
    total_tokens_in = 0
    total_tokens_out = 0
    modules_used = set()
    t0 = time.perf_counter()

    for _round in range(MAX_TOOL_ROUNDS):
        # Use streaming for the final text response, but non-streaming for tool rounds
        response = client.messages.create(
            model=Config.AI_MODEL_CHAT,
            max_tokens=4096,
            system=system,
            tools=TOOLS,
            messages=api_messages,
        )

        total_tokens += (response.usage.input_tokens + response.usage.output_tokens)
        total_tokens_in += response.usage.input_tokens
        total_tokens_out += response.usage.output_tokens

        if response.stop_reason == "tool_use":
            # Tool round — not streaming, execute tools
            api_messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    yield {"type": "status", "content": f"Searching: {block.input.get('query', block.name)}..."}

                    result_text = _execute_tool(block.name, block.input, tenant_id, ticket_id=ticket_id)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_text,
                    })
                    try:
                        data = json.loads(result_text)
                        for r in data.get("results", []):
                            if r.get("module"):
                                modules_used.add(r["module"])
                    except (json.JSONDecodeError, AttributeError):
                        pass

            # Inject round budget so the LLM knows when to wrap up
            remaining = MAX_TOOL_ROUNDS - (_round + 1)
            budget_msg = f"[Tool budget: {_round + 1}/{MAX_TOOL_ROUNDS} used, {remaining} remaining."
            if remaining <= 1:
                budget_msg += " LAST ROUND — respond with your answer NOW."
            elif remaining <= 2:
                budget_msg += " You MUST deliver your final answer on your next response."
            budget_msg += "]"
            tool_results.append({"type": "text", "text": budget_msg})

            api_messages.append({"role": "user", "content": tool_results})
        else:
            # Final response — stream it
            # For true streaming, make a second call with stream=True
            yield {"type": "status", "content": random.choice(_STATUS_PHRASES)}

            sources = _extract_sources(api_messages)

            with client.messages.stream(
                model=Config.AI_MODEL_CHAT,
                max_tokens=4096,
                system=system,
                tools=TOOLS,
                messages=api_messages,
            ) as stream:
                for text in stream.text_stream:
                    yield {"type": "text", "content": text}

                final = stream.get_final_message()
                total_tokens += (final.usage.input_tokens + final.usage.output_tokens)
                total_tokens_in += final.usage.input_tokens
                total_tokens_out += final.usage.output_tokens

            latency = (time.perf_counter() - t0) * 1000
            log_call(caller, tenant_id, ticket_id, "anthropic",
                     Config.AI_MODEL_CHAT, latency, total_tokens_in,
                     total_tokens_out, True)
            _record_usage(tenant_id, ticket_id, "anthropic", Config.AI_MODEL_CHAT, caller,
                          total_tokens_in, total_tokens_out)
            yield {"type": "sources", "sources": sources, "modules_used": list(modules_used)}
            yield {"type": "done", "tokens": total_tokens}
            return

    # Safety: max rounds exceeded — show friendly message in chat (not the raw error)
    latency = (time.perf_counter() - t0) * 1000
    log_call(caller, tenant_id, ticket_id, "anthropic",
             Config.AI_MODEL_CHAT, latency, total_tokens_in,
             total_tokens_out, True)
    _record_usage(tenant_id, ticket_id, "anthropic", Config.AI_MODEL_CHAT, caller,
                  total_tokens_in, total_tokens_out)
    logger.warning("RAG streaming max rounds (%d) exceeded for tenant %s", MAX_TOOL_ROUNDS, tenant_id)
    yield {"type": "text", "content": "I wasn't able to find a specific answer in our knowledge base for this one. Let me connect you with someone who can help."}
    yield {"type": "sources", "sources": _extract_sources(api_messages), "modules_used": list(modules_used)}
    yield {"type": "done", "tokens": total_tokens, "fallback": True}


def _pre_search_kb(
    query: str,
    tenant_id: int,
    module_slug: str | None = None,
    tags: list[str] | None = None,
) -> list[dict]:
    """Run a local KB vector search for context enrichment. Returns parsed results.

    Args:
        module_slug: If set, scope search to this module (from triage).
        tags: If set, pre-filter chunks via GIN index before cosine search.
    """
    try:
        raw = _tool_kb_search(
            query=query, module=module_slug, tags=tags, limit=8, tenant_id=tenant_id,
        )
        data = json.loads(raw)
        return data.get("results", [])
    except Exception as e:
        logger.warning("Pre-search failed (non-fatal): %s", e)
        return []


def _get_tenant_locations(tenant_id: int) -> list[dict]:
    """Fetch active locations for a tenant."""
    rows = fetch_all(
        "SELECT id, name, level_label FROM locations WHERE tenant_id = %s AND is_active = true ORDER BY sort_order, name",
        [tenant_id],
    )
    return rows or []


def generate_response_contextual(
    tenant_id: int,
    messages: list[dict],
    language: str = "en",
    triage_context: dict | None = None,
    l2_analysis: str | None = None,
    persona: str = "agent",
    ticket_context: dict | None = None,
    model_override: str | None = None,
    caller: str = "l1_chat",
    ticket_id: int | None = None,
) -> dict:
    """Generate a contextual AI response with pre-searched KB context and tenant locations.

    Args:
        triage_context: dict from TriageResult with scoped_module_slug, location, etc.
        l2_analysis: If set, L2's deep analysis to inject into system prompt for step-by-step walkthrough.
        ticket_context: If set, structured ticket data injected into system prompt.
    """
    import requests as http_requests
    from config import Config

    # Filter to simple text messages
    simple_messages = [
        {"role": m["role"], "content": m["content"]}
        for m in messages
        if isinstance(m.get("content"), str)
    ]

    # Extract latest user message for pre-search
    latest_query = ""
    for m in reversed(simple_messages):
        if m["role"] == "user":
            latest_query = m["content"]
            break

    turn_count = sum(1 for m in simple_messages if m["role"] == "user")

    # When in ticket-assist mode, use ticket subject+description for KB pre-search
    # instead of vague queries like "analyze" or "thoughts on this case?"
    search_query = latest_query
    if ticket_context and turn_count <= 1:
        tc_subject = ticket_context.get("subject", "")
        tc_desc = ticket_context.get("description", "")
        search_query = f"{tc_subject} {tc_desc}".strip() or latest_query

    # Dev items skip KB pre-search and locations — not relevant for coding tasks
    is_dev_item = (ticket_context or {}).get("ticket_type") in ("task", "bug", "feature")

    # Pre-search KB locally — scope by triage signals when available
    if is_dev_item:
        kb_context = []
        locations = []
    else:
        module_slug = (triage_context or {}).get("scoped_module_slug")
        kb_context = _pre_search_kb(search_query, tenant_id, module_slug=module_slug) if search_query else []
        locations = _get_tenant_locations(tenant_id)

    # Build prior conversation history (all messages except the latest user message)
    conversation_history = simple_messages[:-1] if len(simple_messages) > 1 else None

    # Build enriched system prompt with all context
    system = _build_contextual_system_prompt(
        tenant_id=tenant_id,
        language=language,
        turn_count=turn_count,
        kb_context=kb_context,
        locations=locations,
        conversation_history=conversation_history,
        l2_analysis=l2_analysis,
        persona=persona,
        ticket_context=ticket_context,
    )

    # Direct Claude call with the enriched system prompt
    # Dev items skip tools — no KB search needed for coding tasks
    result = generate_response(tenant_id, messages, language, system_override=system,
                               model_override=model_override, caller=caller,
                               ticket_id=ticket_id, skip_tools=is_dev_item)

    # Always merge pre-search sources — tool-use may not have found them all
    if kb_context:
        existing_sources = result.get("sources") or []
        existing_ids = {s.get("document_id") for s in existing_sources if s.get("document_id")}
        for r in kb_context[:5]:
            if r.get("similarity", 0) > 0.3 and r.get("document_id") not in existing_ids:
                existing_sources.append({
                    "document_id": r.get("document_id"),
                    "title": r.get("title", ""),
                    "url": r.get("source_url", ""),
                    "module": r.get("module", ""),
                })
                existing_ids.add(r.get("document_id"))
        result["sources"] = existing_sources[:5]  # Cap at 5 sources

    return result



def _pick_phrase(used: set) -> str:
    """Pick a status phrase that hasn't been used yet."""
    available = [p for p in _STATUS_PHRASES if p not in used]
    if not available:
        used.clear()
        available = _STATUS_PHRASES
    phrase = random.choice(available)
    used.add(phrase)
    return phrase


def generate_response_stream_contextual(
    tenant_id: int,
    messages: list[dict],
    language: str = "en",
    triage_context: dict | None = None,
    l2_analysis: str | None = None,
    persona: str = "agent",
    ticket_context: dict | None = None,
    caller: str = "l1_chat",
    ticket_id: int | None = None,
):
    """Generate contextual AI response with simulated streaming.

    Sends periodic status messages (~60s apart) while waiting for the LLM,
    never repeating the same phrase.
    """
    import queue
    import threading

    q: queue.Queue = queue.Queue()

    def _worker():
        try:
            result = generate_response_contextual(
                tenant_id, messages, language, triage_context, l2_analysis,
                persona=persona, ticket_context=ticket_context,
                caller=caller, ticket_id=ticket_id,
            )
            q.put(("result", result))
        except Exception as e:
            q.put(("error", e))

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()

    used_phrases: set[str] = set()
    yield {"type": "status", "content": _pick_phrase(used_phrases)}

    while True:
        try:
            kind, payload = q.get(timeout=60)

            if kind == "error":
                logger.error("Stream worker error: %s", payload)
                yield {"type": "text", "content": "Sorry, I ran into an issue. Please try again."}
                yield {"type": "done", "tokens": 0}
                return

            result = payload
            answer = result.get("answer", "")
            chunk_size = 12
            for i in range(0, len(answer), chunk_size):
                yield {"type": "text", "content": answer[i:i + chunk_size]}

            if result.get("sources"):
                yield {"type": "sources", "sources": result["sources"], "modules_used": result.get("modules_used", [])}

            yield {"type": "done", "tokens": result.get("tokens", 0)}
            return

        except queue.Empty:
            # 60 seconds elapsed — send another status message
            yield {"type": "status", "content": _pick_phrase(used_phrases)}


def _build_l2_system_prompt(
    tenant_id: int,
    language: str,
    l1_conversation: list[dict],
    l1_kb_results: list[dict] | None = None,
) -> str:
    """Build enriched system prompt for Layer 2 (Sonnet deep analysis).

    Receives full L1 conversation and KB results so Sonnet can build
    on what Haiku already tried without re-searching the same things.
    """
    tenant_name, module_names = _get_tenant_context(tenant_id)

    lang_instruction = ""
    if language == "es":
        lang_instruction = "\nIMPORTANT: Respond in Spanish (Espa\u00f1ol).\n"

    parts = [
        f"You are a senior technical support specialist for {tenant_name}. "
        f"Knowledge modules: {', '.join(module_names)}.\n",
        "A tier-1 agent was unable to fully resolve the user's issue. "
        "You have the full conversation history and all KB results already found.\n\n",
        "BUDGET: You have exactly 4 tool-call iterations. Plan every call carefully.\n\n"
        "STRATEGY:\n"
        "Iteration 1 — KB Search with a DIFFERENT angle than tier-1 (rephrase, broaden, "
        "use alternate tags or module filters).\n"
        "Iteration 2 — KB Lookup on the most promising result to read the FULL article.\n"
        "Iteration 3 — (optional) One more search or lookup if needed.\n"
        "Iteration 4 — Respond. You MUST deliver your answer by iteration 4.\n\n"
        "RULES:\n"
        "- Batch related lookups in the same iteration when possible.\n"
        "- Do NOT repeat searches tier-1 already ran (see conversation history below).\n"
        "- Provide detailed, step-by-step resolution with article citations.\n"
        "- If unresolvable after research, recommend human escalation with a summary "
        "of everything investigated.\n\n"
        "Be thorough but concise. The user got a quick answer that didn't help — "
        "they need depth and precision.\n",
        f"{lang_instruction}",
    ]

    # Inject L1 conversation history
    if l1_conversation:
        history_lines = []
        for msg in l1_conversation:
            role = "User" if msg.get("role") == "user" else "Tier-1 Agent"
            history_lines.append(f"{role}: {msg.get('content', '')}")
        parts.append("\nTIER-1 CONVERSATION:\n" + "\n".join(history_lines) + "\n")

    # Inject L1 KB results so Sonnet knows what was already found
    if l1_kb_results:
        kb_lines = []
        for r in l1_kb_results[:5]:
            snippet = r.get("content", "")[:300]
            kb_lines.append(f"  [{r.get('title', 'Untitled')}]\n  {snippet}\n")
        parts.append(
            "\nTIER-1 KB RESULTS (already shown to user — search for NEW info):\n"
            + "\n".join(kb_lines)
        )

    return "\n".join(parts)


def generate_response_l2_contextual(
    tenant_id: int,
    l1_conversation: list[dict],
    language: str = "en",
    l1_kb_results: list[dict] | None = None,
) -> dict:
    """Generate L2 (Sonnet) escalated response with deep analysis.

    Args:
        l1_conversation: Full L1 conversation history (all user + assistant messages).
        l1_kb_results: KB results L1 already found (so L2 searches differently).
    """
    import requests as http_requests
    from config import Config

    simple_messages = [
        {"role": m["role"], "content": m["content"]}
        for m in l1_conversation
        if isinstance(m.get("content"), str)
    ]

    system = _build_l2_system_prompt(
        tenant_id=tenant_id,
        language=language,
        l1_conversation=simple_messages,
        l1_kb_results=l1_kb_results,
    )

    # Direct Claude call with the L2 system prompt — uses Sonnet for deep analysis
    result = generate_response(tenant_id, l1_conversation, language, system_override=system,
                               model_override=Config.AI_MODEL_CHAT_L2, caller="l2_chat")
    result["layer"] = 2
    return result


def generate_response_stream_l2_contextual(
    tenant_id: int,
    l1_conversation: list[dict],
    language: str = "en",
    l1_kb_results: list[dict] | None = None,
):
    """Streaming wrapper for L2 escalation. Same pattern as L1 streaming."""
    import queue
    import threading

    q: queue.Queue = queue.Queue()

    def _worker():
        try:
            result = generate_response_l2_contextual(
                tenant_id, l1_conversation, language, l1_kb_results,
            )
            q.put(("result", result))
        except Exception as e:
            q.put(("error", e))

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()

    used_phrases: set[str] = set()
    yield {"type": "status", "content": _pick_phrase(used_phrases)}

    while True:
        try:
            kind, payload = q.get(timeout=60)

            if kind == "error":
                logger.error("L2 stream worker error: %s", payload)
                yield {"type": "text", "content": "Sorry, I ran into an issue. Please try again."}
                yield {"type": "done", "tokens": 0}
                return

            result = payload
            answer = result.get("answer", "")
            chunk_size = 12
            for i in range(0, len(answer), chunk_size):
                yield {"type": "text", "content": answer[i:i + chunk_size]}

            if result.get("sources"):
                yield {"type": "sources", "sources": result["sources"], "modules_used": result.get("modules_used", [])}

            yield {"type": "done", "tokens": result.get("tokens", 0), "layer": 2}
            return

        except queue.Empty:
            yield {"type": "status", "content": _pick_phrase(used_phrases)}


def _to_api_messages(messages: list[dict]) -> list[dict]:
    """Convert simple {role, content} messages to Anthropic API format."""
    api_msgs = []
    for msg in messages:
        if isinstance(msg.get("content"), str):
            api_msgs.append({"role": msg["role"], "content": msg["content"]})
        else:
            api_msgs.append(msg)
    return api_msgs
