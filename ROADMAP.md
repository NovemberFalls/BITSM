# Helpdesk Product Roadmap

> **Goal:** Build the best AI-driven ticketing system in the world.
> Atlas isn't a bolt-on AI — it's the backbone. Every ticket creates data, Atlas learns from it, and the system improves with every case.

**Last updated:** 2026-03-28

---

## Progress Overview

| Phase | Status | Progress |
|-------|--------|----------|
| Foundation (Core Platform) | Complete | 100% |
| Phase 1 — Multi-Model Failover | Complete | 100% |
| Phase 2 — In-Ticket Atlas Experience | Complete | 100% |
| Phase 3 — Auto-Engage + Incident Detection | Complete | 100% |
| Phase 4 — Onboarding + Plan Management | Complete | 100% |
| Phase 5 — Pipeline Orchestration | Complete | 100% |
| Phase 6 — Production Hardening + QA | Complete | 100% |
| Phase 7 — RBAC + Permission System | Complete | 100% |
| Phase 8 — Advanced Atlas Intelligence | Complete | 100% |
| Phase 9 — Reporting + Analytics | Complete | 100% |
| Phase 10 — Platform + Extensibility | Complete | 100% |

**Overall Product Completion: 100%**

---

## Agreed Decisions

| Decision | Detail |
|----------|--------|
| **Primary AI Provider** | Anthropic (Claude Haiku L1, Claude Sonnet L2) |
| **Fallback AI Provider** | OpenAI — automatic failover via `llm_provider.py` for all non-RAG paths; embeddings failover via Voyage→OpenAI |
| **BYOK** | Tenants can bring their own API keys (Anthropic, OpenAI, Voyage) — Fernet-encrypted storage |
| **Product Name** | BITSM — Boord Information Technology Service Management |
| **Pricing Model** | 6 tiers: Free ($0, AI-blocked) / Trial ($0, $15 API/user, Starter-level access) / Starter ($50/user, $15 API/user) / Pro ($100/user, $30 API/user) / Business ($150/user, $45 API/user) / Enterprise BYOK ($100/user flat, tenant BYOK, zero AI COGS) |
| **Orchestration** | Flask pipeline queue (`queue_service.py`) — sequential lanes, cron scheduler, retry with backoff. n8n fully removed. |
| **Duplicate Tickets** | Group as incidents, NEVER merge. Each person keeps their own case |
| **SLA Risk / Routing Metrics** | Manager-only visibility. Never show agents — affects morale |
| **RBAC** | Groups get permissions, users override. Tenant admins assign groups. `@require_permission()` on all routes |
| **AI Audit Navigation** | Permission-based feature, NOT an admin section. Visible to anyone with audit permissions |
| **KB Scrapers** | 15 modules: Toast, Solink, Sonos, PowerBI, Olo, Rockbot, R365, MS (4), Shift4, Lightspeed, Square, Oracle (2), Bill.com, Paytronix |

---

## Foundation — Core Platform (COMPLETE)

- Multi-tenant architecture (tenant isolation on every query)
- PostgreSQL 16 + pgvector + pg_trgm on dedicated `helpdesk` DB
- Flask 3.x backend with 17 blueprints
- React 19 + TypeScript + Zustand frontend (58 components, 8 stores)
- MS365 + Google OAuth with role-based access
- Nova dark/light theme with 5 accent colors
- Docker Compose + Cloudflare Tunnel deployment
- Dev mode with auto-provisioned Dev Tenant/User
- Ticketing: 4 statuses, P1-P4, Kanban + list views, SLA engine, attachments, email/Teams notifications
- Knowledge Base: RAG pipeline (scrape → chunk → embed → pgvector), 15 modules with dedicated scrapers
- Hierarchies: Self-referencing location + category trees, external DB sync, CSV/Excel import
- Customer Portal: Simplified end_user interface, customizable hero/cards, chat widget with conversation history
- Admin Panel: side nav layout — PLATFORM group (Tenants, Pipeline, Token Usage) + WORKSPACE group (Reports, Users, Groups, Locations, Categories, Notifications, Portal, Phone, Billing)
- Location contact info: `phone` and `email` columns on `locations` table, editable inline in TierView
- User ↔ Location assignments: `user_locations` junction table, GET/PUT `/api/admin/users/:id/locations`, cascading tree checklist in user edit form
- Contact Profile system: `contact_profiles` + `contact_location_history` tables — passively builds location history per contact from ticket activity; Atlas uses confidence score to auto-suggest or auto-assign location on engage
- Role display: `super_admin` shown as "Platform Admin" in UI
- Super admin provisioning: `SUPER_ADMIN_DOMAINS` env var — entire email domains auto-provision as super_admin (e.g. `your-domain.com`)
- Structured JSON logging with contextual fields
- Sentry error tracking integration

---

## Phase 1 — Multi-Model Failover (COMPLETE)

- LLM provider abstraction (`services/llm_provider.py`)
- Anthropic primary, OpenAI automatic failover on all `complete()` paths
- Model mapping: Haiku → gpt-4o-mini, Sonnet → gpt-4o
- Cost tracking per-provider in billing_service
- Embedding failover: Voyage AI primary, OpenAI fallback
- Health monitoring endpoint

**Note:** RAG L1 tool-use loop (`rag_service.py`) uses direct Anthropic client — no OpenAI fallback for the agentic KB tool-use loop. All other AI paths (auto-engage, tagging, enrichment, routing, audit, L2 escalation) have full failover.

---

## Phase 2 — In-Ticket Atlas Experience (COMPLETE)

- Atlas tab in ticket detail (4th tab alongside Replies, Agent Notes, All)
- Full ticket context injection (subject, description, location, category, comments)
- Send to Ticket (as reply or internal note)
- Markdown rendering, feedback thumbs, engagement status indicator
- Similar ticket display, category suggestion, suggested agent routing

---

## Phase 3 — Auto-Engage + Incident Detection (COMPLETE)

- KB-aware auto-engage on ticket creation (triage analysis)
- Atlas goes passive when human agent assigned
- Similar ticket detection via pg_trgm
- Incident linking API (parent/child tickets)
- Agent persona vs end-user persona (vendor names only for agents)

---

## Phase 4 — Onboarding + Plan Management (COMPLETE)

- Invite email system via Resend (branded HTML templates)
- OAuth-based invite acceptance flow
- Plan tier management UI (6-tier: Free/Trial/Starter/Pro/Business/Enterprise)
- Onboarding tour (`TourOverlay.tsx`) — auto-shows on first login for new staff users
- AI Audit moved to main sidebar nav (shield icon)

---

## Phase 5 — Pipeline Orchestration (COMPLETE)

**Architecture:** Flask pipeline queue (`services/queue_service.py`) owns all async orchestration. n8n fully removed from codebase.

- **Ticket Create Pipeline** (4 sequential lanes: auto_tag → enrich → engage → route)
- **Ticket Close Pipeline** (2 parallel: audit pipeline, effort score)
- **Notification Pipeline** (Teams Adaptive Card + email dispatch)
- **L1 Chat**: Haiku AI Agent with 3 KB tools (semantic search, chunk lookup, article lookup)
- **L2 Chat**: Sonnet one-shot consultant for escalations
- Up to 5 concurrent LLM workers, configurable via `QUEUE_MAX_LLM_CONCURRENCY`
- Retry with exponential backoff, stale task recovery on processor start

### Built-in Cron Jobs

| Cron | Job | Purpose |
|------|-----|---------|
| `*/15 * * * *` | SLA Breach Monitor | Check SLA breaches, set flag |
| `* * * * *` | SLA Risk Prediction | Identify at-risk tickets (within 1hr of deadline) |
| `30 * * * *` | Escalation Monitor | Auto-escalate P4→P3→P2 if overdue |
| `0 3 * * *` | Audit Auto-Close | Close stale pending audit items |
| `0 6 * * *` | Tenant Health Check | Check tenant expiration, deactivate as needed |
| `0 4 * * 1` | KB Freshness Monitor | Flag KB docs not updated in 90+ days |
| `0 2 * * 0` | KB Pipeline | Scheduled scrape/ingest cycle |
| `0 5 * * *` | Knowledge Gap Detection | Identify topics with tickets but no KB coverage |
| `0 1 * * *` | Trial Expiry | Move tenants past plan_expires_at from trial → free |

---

## Phase 6 — Production Hardening (COMPLETE)

- Portal internal notes hidden from end users
- Markdown rendering in comments (portal + agent)
- Atlas in-ticket chat uses ticket context for KB pre-search
- Caddy removed (Cloudflare Tunnel handles TLS)
- Cloudflare Tunnel live at bitsm.io
- Security hardening: Sentry, Docker hardening, billing cap enforcement
- GFS backup script (`scripts/db_backup.py`) — dumps to Dropbox with daily/weekly/monthly/quarterly/yearly rotation

---

## Phase 7 — RBAC + Permission System (COMPLETE)

**Migration 016**: 5 tables (permissions, groups, group_permissions, user_group_memberships, user_permission_overrides), 15 permission slugs seeded (including `reports.view` added in migration 031), default groups auto-created per tenant.

**Backend:**
- `services/permission_service.py` — resolution order: super_admin bypass → user overrides → group perms → role defaults
- `@require_permission()` decorator on `routes/auth.py`
- Session enriched with `permissions[]` on login (3 code paths)
- 11 new Group/Permission CRUD endpoints on `routes/admin.py`
- 60+ endpoints migrated from `@require_role()` → `@require_permission()` across all blueprints

**Frontend:**
- `hasPermission()` / `hasAnyPermission()` in authStore
- Sidebar + admin tabs gated by permission
- GroupManager component: group CRUD, permission matrix by category, member management

---

## Phase 8 — Advanced Atlas Intelligence (COMPLETE)

**Migration 017**: `sla_risk` column on tickets, `resolution_type`/`ai_turns_before_resolve`/`was_escalated_from_ai`/`high_effort` on ticket_metrics, execution_log table.

**8A — Audit Tier UI**: 4 color-coded tiers (Auto=blue, Human=green, Low Conf=amber, KBA=purple), QualityBar component, TierBadge, tier filter cards with counts.

**8B — Monitoring Cron Jobs**: All 6 monitoring tasks built into Flask pipeline queue (SLA risk, escalation, audit auto-close, KB freshness, tenant health, knowledge gaps). Run via cron scheduler in `queue_service.py`.

**8C — Agent Handoff Summarization**: LLM generates 3-line brief on ticket reassignment, posted as internal note.

**8D — FCR/ROI Tracking**: `_compute_resolution_type()` determines ai_l1/ai_l2/human/hybrid, counts AI turns, tracks escalation. Metrics endpoint returns ai_resolution_rate, escalation_rate, avg_ai_turns, resolution type breakdown.

**8F — Effort Score Enhancement**: Resolution time factor (actual vs SLA), reassignment count factor, `high_effort` flag when score >= 4.0.

---

## Phase 9 — Reporting + Analytics (COMPLETE)

**7 report types** with dedicated components, tier-gated access:

| Report | Access | Component |
|--------|--------|-----------|
| Ticket Volume | Free | `TicketVolume.tsx` |
| Status Breakdown | Free | `StatusBreakdown.tsx` |
| Category Breakdown | Free | `CategoryBreakdown.tsx` |
| SLA Compliance | Paid (Starter+) | `SlaCompliance.tsx` |
| Agent Performance | Paid (Starter+) | `AgentPerformance.tsx` |
| AI Effectiveness | Paid (Starter+) | `AiEffectiveness.tsx` |
| Routing Insights | Paid (Starter+) | `RoutingInsights.tsx` |

**Backend:** `reports_bp` with 8 endpoints, `reports.view` permission slug (migration 031), CSV export support.

**Frontend:** Report card grid with tier badges, `DateRangePicker` shared filter, `ReportTable` generic renderer with CSV export, ECharts for visualizations.

---

## Phase 10 — Platform + Extensibility (COMPLETE)

### 10A. Billing & BYOK (COMPLETE)
- `services/billing_service.py` — API usage tracking, monthly cost rollup, per-tier caps
- `check_ai_gate(tenant_id)` — blocks free tier, raises `ApiCapError` over cap
- BYOK key storage: Fernet-encrypted Anthropic/OpenAI/Voyage keys per tenant
- `record_usage()` instrumented on all AI call sites (atlas, rag, tagging, enrichment, embedding)
- `UsagePanel.tsx` — admin tab with token consumption stats
- Migrations 027-029 (usage tracking), 033 (6-tier enum)

### 10B. Workflow Automations (COMPLETE)
- Visual workflow builder (`AutomationBuilder.tsx`) using React Flow
- 4 node types: Trigger, Condition, Action, Comment
- **Triggers**: ticket_created, status_changed, priority_changed, comment_added, assignee_changed, tag_added, sla_breached, schedule
- **Conditions**: priority_is, status_is, category_is, location_is, tag_contains, assignee_set, requester_role, hours_since
- **Actions**: assign_to, change_priority, change_status, add_tag, remove_tag, post_comment, send_notification, webhook
- `automation_engine.py` — event evaluation, condition matching, action execution
- 7 API endpoints, run history tracking, test automation on sample ticket
- Migration 032

### 10C. Phone Service AI (COMPLETE)
- [x] `phone_bp` registered — `/api/phone/*` endpoints live
- [x] Config CRUD: `GET/PUT /api/phone/config`, provision endpoints (EN + ES)
- [x] IVR greeting + routing: `/api/phone/ivr/:tenant_id` (GET/POST + `/route`)
- [x] Atlas tool endpoints: `search_kb`, `create_ticket`, `identify_caller`, `attempt_transfer`, `collect_email`
- [x] Session tracking: `phone_sessions`, `phone_transfer_attempts` tables
- [x] Call webhook: `POST /api/phone/webhook/:tenant_id/call_ended` (HMAC-authenticated)
- [x] `PhoneSettings.tsx` admin panel component (multi-agent two-panel UI with deploy stepper)
- [x] ElevenLabs ConvAI integration — `deploy_agent()` creates/updates agents, Fernet-encrypted credentials, platform + BYOK key resolution
- [x] Twilio auto-provisioning — `auto_provision()` buys number, provisions EL agent, links, activates in one call
- [x] Per-session cost tracking — `_fetch_el_conversation_cost()` (EL credits + LLM tokens) + `_fetch_twilio_call_cost()` (Twilio cents), stored in `phone_sessions`
- [x] E2E tested with Dev Tenant — Atlas (EN, digit 1) + Astra (ES, digit 2) live, Twilio number auto-purchased and linked

### 10D. Portal Enhancements (COMPLETE)
- Chat widget with conversation history, new/previous chat selection, status filters
- Self-service KB search with suggested articles
- Article effectiveness feedback (thumbs up/down)
- Auto-create case on first chat exchange
- Inactivity detection (10-minute timeout with warning)
- [x] Status page — `status_incidents` + `status_incident_updates` tables (migration 053), `status_bp` blueprint with CRUD + timeline updates, `StatusPage.tsx` portal view (severity badges, expandable timeline, "All Systems Operational" banner), `StatusPageAdmin.tsx` admin panel (create/edit/delete incidents, post timeline updates), default "System Status" portal card

### 10E. Notifications System (COMPLETE)
- Notification groups (Teams channel recipients) with CRUD
- Group × Event matrix — which events go to which groups
- Email template customization per event per tenant
- 9 event types: ticket_created, assigned, resolved, closed, status_changed, agent_reply, requester_reply, sla_warning, sla_breach
- Migrations 034-035

### 10F. Inbound Email (COMPLETE)
- Cloudflare Email Worker (`email-worker/`) receives inbound emails
- MIME parsing via postal-mime, forwards to `/api/webhooks/inbound-email`
- Migration 036

### 10G. Integrations (COMPLETE)
- [x] Slack webhook notifications — `_send_slack_notification()` in `notification_service.py`, Block Kit format, webhook URL config in admin Notification settings, dispatch alongside Teams/email channels
- PagerDuty and Jira/Linear — deferred (existing Teams/Slack/Email/Twilio channels cover alerting needs)

---

## Knowledge Base Modules (15)

| Module | Scraper Type | Approx. Docs | Status |
|--------|-------------|--------------|--------|
| Toast | — | 2,330 | Active |
| Solink | Intercom (Next.js) | 201 | Active |
| Sonos | Zendesk API | 380 | Active |
| PowerBI | GitHub Tarball | 1,261 | Active |
| Olo | Zendesk API | 97 | Active |
| Rockbot | Zendesk API | 144 | Active |
| Restaurant365 | Zendesk API | 346 | Active |
| MS Outlook | Sitemap + async | 140 | Active |
| MS Teams | Sitemap + async | 131 | Active |
| MS Excel | Sitemap + async | 35 | Active |
| MS SharePoint | Sitemap + async | 137 | Active |
| Shift4/SkyTab | Zendesk API | ~2,100 | Active |
| Lightspeed | Zendesk API | — | Active |
| Square | Sitemap + async | ~300 | Active |
| Oracle Simphony | Static HTML TOC | ~174 | Active |
| Oracle Xstore | Static HTML TOC | ~500 | Active |
| Bill.com | Salesforce (Playwright) | ~1,000 | Active |
| Paytronix | Salesforce (Playwright) | ~14 | Active |

**Total: ~9,000+ documents across 15 scrapers (18 module slugs)**

---

## Database Migrations (53 applied)

| ID | Purpose |
|----|---------|
| 001 | Core schema (tenants, users, knowledge_modules, documents, pgvector) |
| 002 | Ticket overhaul (tickets, comments, locations, categories, tags) |
| 003 (×2) | Ticket enhancements + location DB sync |
| 004 | Tenant articles + auth |
| 005 | Email notifications |
| 006 | RAG pipeline (document_chunks, embeddings) |
| 007 | AI chat module (conversations, messages) |
| 008-010 | Document/chunk tagging |
| 011 | Sonos module |
| 012 | Article recommendations |
| 013 | Admin panel rework |
| 014 | AI intelligence (module_features, atlas_engagements, audit_queue, knowledge_gaps, ticket_metrics) |
| 015 | Incident linking + plan tiers |
| 016 (×2) | Olo/Rockbot modules + RBAC (permissions, groups, overrides) |
| 017 | Phase 8 (sla_risk, resolution_type, effort tracking) |
| 018 | Notification external emails |
| 019 | R365/Microsoft modules |
| 020 | Audit auto statuses |
| 021 | Pipeline queue (pipeline_queue, execution_log, schedules) |
| 022 (×2) | KB scrapers batch 2 + pipeline phases |
| 023 | Ticket attachments |
| 024 | Source/inbound channel tracking |
| 025 | Execution log output column |
| 026 | Article feedback |
| 027-029 | Token usage tracking (api_usage_log, cost rates) |
| 030 | Article effectiveness metrics |
| 031 | Reports permission slug |
| 032 | Automations (automations, runs, canvas) |
| 033 | Pricing tiers (6-value enum) |
| 034 | Notification group events |
| 035 | Notification templates |
| 036 | Inbound email |
| 037 | Salesforce scrapers (Bill.com, Paytronix modules) |
| 023_contact_profiles | Contact profiles + contact_location_history (passive location roster) |
| 024_location_contact_info | phone/email on locations + user_locations junction table |
| 038 | File upload metadata on documents (source_file_name, source_file_type, file_size) |
| 039 | Tenant collections (tenant-scoped article groupings) + documents FK |
| 040 | Collection URL-safe slugs (unique per tenant) |
| 041 | Teams: team table, team_members, ticket team assignment |
| 042 | Dev tickets: ticket_type, sprints, task checklists, status workflows |
| 043 | Team-scoped problem categories (team_id FK) |
| 044 | RBAC permissions audit: seeds missing permissions, grants to admin/manager groups |
| 045 | Phone config AI columns (llm_model, temperature, turn_timeout, audio_format) + sub-features |
| 046 | Team event notification subscriptions |
| 047 | Multi-agent phone system: phone_agents table, session FK |
| 048 | IVR greeting customization: ivr_greeting_en/es on phone_configs |
| 049 | Agent-driven IVR: ivr_greeting on phone_agents, migrates en/es greetings |
| 050 | Work item types table (system defaults), tickets.work_item_type_id FK, completed_at/completed_by |
| 051 | Expand tickets_status_check for dev statuses (backlog, todo, in_progress, etc.) |
| 052 | Agile hierarchy: acceptance_criteria, parent_id, work_item_number, sort_order, capacity_points |
| 053 | Status Page: status_incidents + status_incident_updates tables |

---

## QA Audit — Phase-by-Phase Verification

Walk through each section below. Test on https://bitsm.io (production) and/or local dev. Mark each item pass/fail.

### Foundation QA

**Auth & Session**
- [ ] Login via MS365 OAuth → redirects back, session established
- [ ] Login via Google OAuth → redirects back, session established
- [ ] Dev mode (`AUTH_ENABLED=false`) → auto-provisions Dev User + Dev Tenant
- [ ] Logout → clears session, redirects to login page
- [ ] Role-based navigation: super_admin sees all, tenant_admin sees admin, agent sees tickets/chat, end_user sees portal

**Multi-Tenancy**
- [ ] Tenant A cannot see Tenant B's tickets, users, or KB articles
- [ ] Super admin sees all tenants' data
- [ ] Tenant admin is scoped to their own tenant
- [ ] API endpoints enforce `tenant_id` on every query

**Ticketing**
- [ ] Create ticket → auto-generates ticket number (TKT-XXXXX)
- [ ] Set priority (P1-P4) → correct badge/color
- [ ] Set status transitions: Open → Pending → Resolved → Closed NR
- [ ] Assign agent → agent appears in assignee dropdown
- [ ] Add reply comment → visible to requester
- [ ] Add internal note → NOT visible to end_user
- [ ] Tag suggestions appear (LLM auto-tag fires on create)
- [ ] Accept/reject tag suggestions
- [ ] Kanban board shows 4 columns (Open, Pending, Resolved, Closed NR)
- [ ] List view toggle works
- [ ] Click ticket card → detail panel opens smoothly (no flash)
- [ ] Ticket detail shows all 4 tabs (Replies, Agent Notes, Atlas, All)
- [ ] File attachments upload/download

**Knowledge Base**
- [ ] KB browser lists documents from tenant's enabled modules
- [ ] Document viewer shows full content with markdown rendering
- [ ] Search works across titles and tags
- [ ] Tag filter shows documents matching selected tag
- [ ] Article suggestions appear on ticket detail (based on subject/tags)

**Hierarchies**
- [ ] Location tree displays correctly (parent/child nesting)
- [ ] Create location → appears in tree
- [ ] Edit location name → updates immediately
- [ ] Delete location → soft-delete (is_active=false)
- [ ] Problem category tree displays correctly
- [ ] Create/edit/delete problem categories
- [ ] Cascading select on ticket form works (pick parent → children load)
- [ ] Tenant-scoped: admin only sees own tenant's hierarchies

**Customer Portal**
- [ ] Navigate to `/<slug>/portal` → portal loads (no sidebar, no admin nav)
- [ ] Portal hero section displays with customizable greeting
- [ ] "Report an Issue" card → opens ticket creation form
- [ ] "Check Ticket Status" → shows My Cases list
- [ ] End user sees only their own tickets
- [ ] Internal notes are NOT visible in portal view
- [ ] Markdown renders correctly in portal comments
- [ ] Chat widget appears at bottom right
- [ ] Chat widget shows conversation history (new/previous chat selection)
- [ ] Chat widget status filters work (All, Open, Resolved, No Case)
- [ ] Article feedback ratings work (thumbs up/down)

**Admin Panel**
- [ ] Side nav renders two groups: PLATFORM (super_admin only) and WORKSPACE
- [ ] Tenants tab: list, create, update, deactivate tenants (super_admin only)
- [ ] Users tab: list users, invite new user (sends email), update role, deactivate
- [ ] Users tab: Edit user → Assigned Locations section shows nested tree; clicking parent selects all children
- [ ] Users tab: Role column shows "Platform Admin" (not raw `super_admin`)
- [ ] Groups tab: group CRUD, permission matrix, member management
- [ ] Locations tab: tree manager; edit a location node → phone and email fields appear inline
- [ ] Categories tab: tree manager for problem categories
- [ ] Notifications tab: groups, event matrix, email templates, settings
- [ ] Portal tab: portal settings (greeting, background, cards, logo)
- [ ] Pipeline tab: queue stats, active tasks, failures, schedules
- [ ] Token Usage tab: consumption stats
- [ ] Reports tab: "Coming soon" placeholder card visible
- [ ] KB module stats show doc/chunk counts on tenant module cards

**Theme**
- [ ] Dark/light mode toggle works (sun/moon icon)
- [ ] 5 accent colors (green, red, gold, blue, white) switch correctly
- [ ] All components respect theme CSS variables

---

### Phase 1 QA — Multi-Model Failover

- [ ] AI chat works with Anthropic key configured (Haiku L1)
- [ ] Non-RAG AI services fallback to OpenAI when Anthropic fails (tagging, enrichment, routing, audit, L2)
- [ ] Embedding service falls back from Voyage to OpenAI
- [ ] Error messages are user-friendly (not raw stack traces)
- [ ] Health endpoint: `GET /api/webhooks/health` returns `{"status": "ok"}`

---

### Phase 2 QA — In-Ticket Atlas

- [ ] Open a ticket → click Atlas tab → chat interface loads
- [ ] Type a question → Atlas responds with KB-sourced answer
- [ ] Response includes source references (article titles)
- [ ] Markdown renders in Atlas responses (bold, lists, code blocks)
- [ ] "Send to Ticket" → creates reply or internal note on the ticket
- [ ] Feedback thumbs (up/down) → persists to DB
- [ ] Engagement status dot: green (Active), amber (Passive), gray (Closed)
- [ ] Similar tickets section shows related tickets with confidence
- [ ] Category suggestion shows with confidence percentage
- [ ] Suggested agent shows with confidence score

---

### Phase 3 QA — Auto-Engage + Incidents

- [ ] Create a new ticket (with AI Ticket Review enabled for tenant) → Atlas auto-posts internal note with triage analysis
- [ ] Triage note includes: category suggestion, priority assessment, next steps
- [ ] Assign human agent → Atlas status changes to Passive
- [ ] Atlas still responds if asked directly while passive
- [ ] Similar ticket detection: create ticket with similar subject → similar tickets appear
- [ ] Incident linking: POST `/api/ai/tickets/{id}/link-incident` → links child to parent
- [ ] Unlink incident → removes parent_ticket_id
- [ ] Agent persona: Atlas references vendor names (Toast, Solink, etc.)
- [ ] End-user persona: Atlas does NOT reference vendor names

---

### Phase 4 QA — Onboarding + Plans

- [ ] Admin → Users → Invite → enter email + role → invite email sends
- [ ] Invite email contains branded HTML with accept link
- [ ] Click accept link → OAuth flow → user account activated
- [ ] Resend invite → resets invite_at, extends expires_at
- [ ] Bulk import: upload CSV with email, first_name, last_name, role → creates users
- [ ] Plan tier: super_admin can set Free/Trial/Starter/Pro/Business/Enterprise on tenant
- [ ] Plan expiration: set expiration date → shows in admin
- [ ] Extend plan: "extend by X days" button works
- [ ] Onboarding tour fires on first login for new staff users

---

### Phase 5 QA — Pipeline Orchestration

- [ ] Create ticket → pipeline queue fires (auto_tag → enrich → engage → route)
  - [ ] Auto-tag runs
  - [ ] Enrichment runs
  - [ ] Auto-engage runs (if ticket_review enabled)
  - [ ] Routing suggestion runs
- [ ] Close ticket → pipeline fires (audit pipeline + effort score)
  - [ ] Audit pipeline produces audit queue entry
  - [ ] Effort score is calculated
- [ ] Notification events dispatch via pipeline
  - [ ] Teams Adaptive Card on P1/P2 ticket creation
  - [ ] Email notification on ticket reply (if preference enabled)
- [ ] SLA Breach Monitor cron: overdue ticket → SLA breach flag set
- [ ] L1 Chat: question → Haiku responds with KB tool-use loop
- [ ] L2 Chat: escalation → Sonnet one-shot response
- [ ] Pipeline admin tab shows queue stats, active tasks, execution history

---

### Phase 6 QA — Production Hardening

- [ ] Portal: internal notes NOT visible to end_user
- [ ] Portal: markdown renders in comments (not raw `**bold**`)
- [ ] Agent view: markdown renders in ticket comments
- [ ] Atlas in-ticket: uses ticket subject + description for KB pre-search (not blank)
- [ ] Production health: `curl https://bitsm.io/api/webhooks/health` → ok
- [ ] Sentry error tracking captures exceptions

---

### Phase 7 QA — RBAC

**Schema**
- [ ] `permissions` table exists with 15 slugs
- [ ] `groups` table exists with default groups per tenant
- [ ] `group_permissions` table links groups → permissions
- [ ] `user_group_memberships` table links users → groups
- [ ] `user_permission_overrides` table exists

**Permission Resolution**
- [ ] Super admin always has all permissions (bypass)
- [ ] User with no groups → falls back to role defaults (agent gets tickets.view, tickets.create, etc.)
- [ ] User in "Managers" group → gets metrics.view, audit.review, etc.
- [ ] User override `granted=false` on audit.view → denies audit.view even if group grants it
- [ ] User override `granted=true` on audit.review → grants it even if no group has it

**API Endpoints**
- [ ] `GET /api/admin/permissions` → returns all permissions
- [ ] `GET /api/admin/groups` → returns groups for current tenant
- [ ] `POST /api/admin/groups` → creates new group
- [ ] `PUT /api/admin/groups/{id}` → updates group name/description
- [ ] `DELETE /api/admin/groups/{id}` → soft-deletes, moves members to default
- [ ] Cannot delete default group → returns 400
- [ ] `GET /api/admin/groups/{id}/permissions` → returns group's permission slugs
- [ ] `PUT /api/admin/groups/{id}/permissions` → replaces permissions (full set)
- [ ] `GET /api/admin/groups/{id}/members` → returns member list
- [ ] `PUT /api/admin/groups/{id}/members` → replaces member list
- [ ] `GET /api/admin/users/{id}/permissions` → returns effective perms + overrides
- [ ] `PUT /api/admin/users/{id}/permissions/overrides` → sets overrides

**Route Guards**
- [ ] Agent without `audit.view` → cannot access `/audit` page (403 or redirect)
- [ ] Agent without `audit.view` → `GET /api/audit/queue` returns 403
- [ ] Tenant admin scoped to own tenant (cannot see other tenants' groups)
- [ ] Super admin can see all tenants' groups (with `?tenant_id=X` filter)

**Frontend**
- [ ] Sidebar: AI Audit only visible if user has `audit.view`
- [ ] Sidebar: Admin section only visible if user has relevant admin permissions
- [ ] Admin → Groups tab: visible when user has `users.manage`
- [ ] GroupManager: create group → appears in list
- [ ] GroupManager: select group → permission matrix loads with checkboxes
- [ ] GroupManager: toggle permission → saves on click
- [ ] GroupManager: add/remove member → updates member list
- [ ] Session includes `permissions[]` array (check browser dev tools → session/user object)

---

### Phase 8 QA — Advanced Atlas Intelligence

**8A — Audit Tier UI**
- [ ] Audit queue items show TierBadge: Auto (blue), Human (green), Low Conf (amber), KBA (purple)
- [ ] QualityBar shows colored progress: red < 40%, amber 40-70%, green > 70%
- [ ] Filter buttons show colored dots and per-tier counts
- [ ] KBA draft displays in purple left-border card with "KBA DRAFT" header

**8B — Monitoring Cron Jobs**
- [ ] SLA risk detection runs and identifies at-risk tickets
- [ ] Escalation check auto-bumps priority on overdue tickets
- [ ] Audit auto-close clears stale pending items
- [ ] KB freshness monitor flags old docs
- [ ] Tenant health check detects expired/inactive tenants
- [ ] Knowledge gap detection identifies uncovered topics

**8C — Handoff Summarization**
- [ ] Reassign ticket (change assignee from Agent A to Agent B)
- [ ] Atlas posts internal note with `[Atlas Handoff Summary]` prefix
- [ ] Summary contains 3 lines: what's been tried, customer sentiment, likely root cause
- [ ] First assignment (no previous assignee) does NOT trigger handoff summary

**8D — FCR/ROI Tracking**
- [ ] Close a ticket → `ticket_metrics.resolution_type` is populated (ai_l1/ai_l2/human/hybrid)
- [ ] `ticket_metrics.ai_turns_before_resolve` has correct count of AI assistant messages
- [ ] `ticket_metrics.was_escalated_from_ai` = true if L2 escalation occurred
- [ ] `GET /api/audit/metrics` returns: `ai_resolution_rate`, `escalation_rate`, `avg_ai_turns`
- [ ] Metrics include breakdown: `ai_l1_count`, `ai_l2_count`, `human_count`, `hybrid_count`

**8F — Effort Score Enhancement**
- [ ] Close a ticket → effort_score accounts for resolution time vs SLA target
- [ ] Tickets with reassignments have higher score (+0.5 each, capped)
- [ ] `ticket_metrics.high_effort` = true when effort >= 4.0
- [ ] `GET /api/audit/metrics` returns `high_effort_count`

---

### Phase 9 QA — Reporting

- [ ] Reports view accessible from sidebar (requires `reports.view` permission)
- [ ] Free reports load: Ticket Volume, Status Breakdown, Category Breakdown
- [ ] Paid reports gated by plan tier: SLA Compliance, Agent Performance, AI Effectiveness, Routing Insights
- [ ] Date range picker filters all reports
- [ ] CSV export works for all report tables
- [ ] Charts render correctly (ECharts)

---

### Phase 10 QA — Platform

**Billing & BYOK**
- [ ] Free tier blocks AI access (`check_ai_gate` returns 402)
- [ ] Token usage tracked per AI call (check `api_usage_log` table)
- [ ] Monthly rollup correct (`api_usage_monthly`)
- [ ] Over-cap tenant gets blocked on user-facing AI, background ops continue
- [ ] BYOK keys encrypt/decrypt correctly (Fernet)
- [ ] UsagePanel admin tab shows consumption data

**Automations**
- [ ] Automation list shows all automations with toggle
- [ ] Create automation → opens builder canvas
- [ ] Drag/drop nodes from palette (trigger, condition, action, comment)
- [ ] Connect nodes with edges (delete by click)
- [ ] Configure node properties in side panel
- [ ] Save automation → persists canvas + config
- [ ] Toggle active/inactive
- [ ] Test automation on sample ticket
- [ ] Run history shows execution timeline

**Notifications**
- [ ] Create notification group → add members (users + external emails)
- [ ] Group × Event matrix: toggle which events go to which groups
- [ ] Email template customization per event type
- [ ] Reset template to default
- [ ] Global settings (blocklist, loop detection, Teams webhook)

**Inbound Email**
- [ ] Email worker receives inbound email (Cloudflare Email Routing)
- [ ] Webhook creates/updates ticket from parsed email

---

### Cross-Cutting QA

**Performance**
- [ ] Page loads in < 2 seconds (first paint)
- [ ] Ticket list loads without visible flash (cached data on revisit)
- [ ] Navigating between views is seamless (no full page reload)
- [ ] AI chat streaming works (tokens appear incrementally, not all at once)

**Security**
- [ ] SQL injection: no raw string interpolation in queries (parameterized only)
- [ ] XSS: user input rendered safely (React escapes by default)
- [ ] Auth: all API endpoints behind `@login_required` or `@require_permission`
- [ ] Tenant isolation: cross-tenant data access impossible
- [ ] Connectors: secrets encrypted with Fernet
- [ ] CSRF: session-based auth with same-site cookies

**Error Handling**
- [ ] Invalid API request → proper error JSON with message (not 500 traceback)
- [ ] Missing required field → 400 with clear error
- [ ] Unauthorized access → 401 or 403 with message
- [ ] Nonexistent resource → 404

**Data Integrity**
- [ ] Soft deletes: tenants, locations, categories, groups use `is_active=false`
- [ ] Tickets never deleted (status transitions only)
- [ ] Cascade deletes: deleting tenant cascades to groups/memberships
- [ ] Unique constraints: no duplicate permission slugs, no duplicate group names per tenant

---

## What's Left

**Core product: 100% complete.** All 10 phases shipped.

### Completed (2026-03-28)
- **Status Page** — migration 053, `status_bp` blueprint, `StatusPage.tsx` portal view (severity badges, expandable timeline), `StatusPageAdmin.tsx` admin CRUD, default "System Status" portal card
- **Slack Webhook Notifications** — `_send_slack_notification()` with Block Kit format, admin UI, dispatch alongside Teams/email
- **Security Hardening** — Twilio signature validation on IVR, HMAC token auth on call_ended, tenant-scoped ticket reads
- **Legal Updates** — ElevenLabs/Twilio/Stripe as sub-processors in Privacy Policy + DPA, voice/telephony + billing data sections
- **Launch Readiness** — SEO meta tags, robots.txt, tenant admin setup checklist (6-step activation)
- **Infra Audit** — Nadia (coding team) audited entire codebase: removed 60+ dead CSS selectors, resolved 3 duplicate conflicting CSS blocks, replaced 48+ hardcoded colors with Nova theme variables, added 5 missing CSS classes, removed 24 dead API client methods + 2 unused types, deleted obsolete n8n docs, cleaned stale env vars
- **Cron Scheduler Fix** — All 9 pipeline cron tasks were silently broken (2-7 days stale). Root cause: `datetime.now()` (naive) vs DB TIMESTAMPTZ (UTC-aware) caused `_is_cron_due()` to silently return False. Fixed with `datetime.now(timezone.utc)`, added `pg_advisory_lock` for multi-worker safety, added exception logging. All tasks verified running post-deploy.

### Nice-to-Have
- Agent Performance Digest (weekly cron email to managers)
- RAG Effectiveness weekly report
- Tenant health super-admin dashboard
- Knowledge gap heatmap visualization

### Deferred
- Salesforce scrapers that need auth (Revel, TouchBistro, Clover) — Playwright infrastructure now in place

---

## Appendix: Why This Wins

1. **Atlas is the backbone, not a bolt-on.** Create → auto-engage → assist → audit → learn. Every other platform added AI as a feature. We built it as the lifecycle.

2. **The audit loop closes the gap.** Most ticketing systems are write-only. Atlas auditing every closed ticket means the system improves with every case.

3. **Tiered AI cost model.** Haiku at ~$0.04/turn for 80% of cases, Sonnet at ~$0.20 for the hard ones. ~$480/month for 100 daily conversations.

4. **Pipeline queue as the nervous system.** Every async event is tracked, retryable, and auditable. Sequential lanes guarantee correct processing order. Cron scheduler handles all monitoring.

5. **Incident intelligence.** Duplicate detection groups related tickets as incidents. Knowledge gap detection auto-generates the content creation priority list.

6. **RBAC for real organizations.** Groups + permissions + overrides. A 50-agent tenant can have L1/L2/L3 groups with different capabilities. Manager metrics invisible to agents.

7. **15 KB scrapers.** Toast, Solink, Sonos, PowerBI, Olo, Rockbot, R365, Microsoft (4), Shift4, Lightspeed, Square, Oracle (2), Bill.com, Paytronix. ~9,000+ documents feeding Atlas.
