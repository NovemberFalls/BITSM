# BITSM Migrations Reference
All applied migrations. See [README.md](../README.md) for architecture overview.

| Migration | Purpose |
|-----------|---------|
| `001_core_schema.sql` | 13 base tables + pgvector, seeds 4 knowledge modules |
| `002_ticket_overhaul.sql` | Locations, problem categories, tag suggestions |
| `003_ticket_enhancements.sql` | Ticket system enhancements |
| `003_location_db_sync.sql` | Location DB sync support |
| `004_tenant_articles_and_auth.sql` | Tenant articles and auth improvements |
| `005_email_notifications.sql` | Email notification support |
| `006_rag_pipeline.sql` | RAG pipeline tables |
| `007_ai_chat_module.sql` | AI chat module setup |
| `008_document_tags.sql` | Document tagging |
| `009_chat_refactor.sql` | Chat system refactor |
| `010_chunk_tags.sql` | Chunk-level tagging |
| `011_sonos_module.sql` | Sonos knowledge module |
| `012_article_recommendations.sql` | Article recommendation tracking |
| `013_admin_panel_rework.sql` | Admin panel restructure |
| `014_ai_intelligence.sql` | AI intelligence: module_features, tenant_module_features, atlas_engagements, ticket_audit_queue, knowledge_gaps, ticket_metrics |
| `015_incident_linking_and_tiers.sql` | Incident linking (parent_ticket_id), tenant plan tiers (plan_tier/expires_at), pg_trgm for similar ticket search, atlas_engagement enhancements |
| `016_olo_rockbot_modules.sql` | Olo and Rockbot knowledge modules |
| `019_r365_microsoft_modules.sql` | Restaurant365, MS Outlook, MS Teams, MS Excel, MS SharePoint knowledge modules |
| `022_kb_scrapers_batch2.sql` | Shift4, Lightspeed, Square, Oracle Simphony, Oracle Xstore modules + kb_pipeline cron schedule |
| `016_rbac.sql` | RBAC: permissions, groups, group_permissions, user_group_memberships, user_permission_overrides. Seeds initial 14 permission slugs (expanded to 28 across later migrations), creates default groups per tenant |
| `017_phase8_atlas.sql` | Phase 8: sla_risk column on tickets, resolution_type/ai_turns/was_escalated/high_effort on ticket_metrics, execution_log table, audit queue auto_closed status |
| `023_contact_profiles.sql` | Contact profiles (passive location roster): contact_profiles, contact_location_history tables |
| `024_location_contact_info.sql` | Location phone/email columns + user_locations junction table (user ↔ location assignments) |
| `038_uploaded_articles.sql` | File upload metadata on documents: source_file_name, source_file_type, file_size |
| `039_tenant_collections.sql` | Tenant collections table (tenant-scoped article groupings) + documents.tenant_collection_id FK |
| `040_collection_slugs.sql` | URL-safe slug column on tenant_collections (unique per tenant) |
| `041_teams.sql` | Teams: team table, team_members, ticket team assignment, team-scoped filters |
| `042_dev_tickets.sql` | Dev tickets: ticket_type, sprints, task checklists, status workflows |
| `043_team_categories.sql` | Team-scoped problem categories (team_id FK on problem_categories) |
| `044_rbac_audit.sql` | RBAC permissions audit: seeds missing permissions (phone.manage, etc.), grants to admin/manager groups |
| `045_phone_config_expansion.sql` | Phone config AI columns (llm_model, temperature, turn_timeout, audio_format) + phone_support module sub-features (5 toggles) |
| `046_team_event_subscriptions.sql` | Team event notification subscriptions |
| `047_phone_agents.sql` | Multi-agent phone system: phone_agents table (N agents per tenant), phone_sessions.phone_agent_id FK, migrates existing Atlas/Astra from phone_configs into phone_agents rows |
| `048_ivr_greeting_fields.sql` | IVR greeting customization: ivr_greeting_en, ivr_greeting_es columns on phone_configs, populates assigned_phone_number for tenant 1 |
| `049_agent_ivr_greeting.sql` | Agent-driven IVR: ivr_greeting column on phone_agents, migrates existing en/es greetings from phone_configs to matching agents |
| `050_sprint_enhancements.sql` | Work item types table (system defaults: Epic/Story/Task/Bug/Sub-task with icons+colors), tickets.work_item_type_id FK, tickets.completed_at/completed_by columns |
| `051_status_check_expand.sql` | Expands tickets_status_check constraint to include dev statuses (backlog, todo, in_progress, in_review, testing, done, cancelled). Requires superuser. |
| `052_work_item_fields.sql` | Agile hierarchy: acceptance_criteria, parent_id (hierarchy FK, separate from parent_ticket_id for incidents), work_item_number (WI-#####) + sequence, sort_order for ranking, capacity_points on sprints, allowed_parent_slugs on work_item_types |
| `053_status_page.sql` | Status Page: status_incidents (title, body, status, severity, scheduled_end, resolved_at) + status_incident_updates (timeline entries per incident) |
| `063_messaging.sql` | SMS + WhatsApp: extends phone_configs with sms_enabled/whatsapp_enabled/whatsapp_phone_number/whatsapp_status/auto_reply/auto_create_ticket/default_language. Creates messaging_conversations (threaded by tenant+channel+phone), messages (direction/channel/status/cost/segments/template), messaging_templates (WhatsApp approved templates). Adds `messaging.manage` permission. |
| `064_inapp_notifications_and_csat.sql` | In-app notifications + CSAT survey support |
| `065_ticket_activity.sql` | Ticket activity tracking |
| `066_audit_events.sql` | Audit events table (insert-only, 6 indexes, 2-year retention) |
| `067_tenant_ticket_numbering.sql` | Per-tenant ticket number sequences |
| `068_ticket_number_per_tenant_unique.sql` | Unique constraint on tenant + ticket number |
| `069_api_key_expiry.sql` | API key expiry: `expires_at` column on api_keys, 90-day default |
| `070_audit_retention_index.sql` | Audit retention index for cleanup queries |
| `071_custom_forms.sql` | Form templates / service catalog: `form_templates` table (name, ticket_type, field_ids[], catalog_category, is_active, sort_order), `custom_field_definitions` additions (parent_field_id, show_when JSONB for conditional fields), `ticket_form_settings` for per-type config |
| `072_template_subject_format.sql` | `subject_format` TEXT column on form_templates for `{{field_key}}` variable interpolation |
