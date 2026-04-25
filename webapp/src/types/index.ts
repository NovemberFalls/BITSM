// User profile (returned by GET /api/auth/profile and PUT /api/auth/profile)
export interface UserProfile {
  id: number;
  name: string;
  email: string;
  role: string;
  phone_number: string | null;
  sms_opted_in: boolean;
  sms_opted_in_at: string | null;
}

// Global app config injected by Flask template
export interface AppConfig {
  mode: 'dashboard' | 'tickets' | 'kb' | 'chat' | 'admin' | 'portal';
  user: AppUser;
  app_name: string;
  app_url: string;
  ticket_id?: number;
  section?: string;
  tenant_settings?: TenantSettings;
  idle_timeout_minutes?: number;
  csrf_token?: string;
  ai_chat_enabled?: boolean;
  ai_features?: AIFeatures;
  tenant_slug?: string;
  tenant_name?: string;
  tenant_logo_url?: string;
  // Demo / BYOK plan fields (Tier 2)
  demo_mode?: boolean;
  trial_expires_at?: string | null;
  byok_configured?: boolean;
}

export interface AIFeatures {
  ticket_review?: boolean;
  agent_chat?: boolean;
  client_chat?: boolean;
  phone_service?: boolean;
}

export interface TicketFormSettings {
  subject_required?: boolean;
  description_required?: boolean;
  location_required?: boolean;
  category_required?: boolean;
}

export interface TenantSettings {
  problem_field_label?: string;
  location_levels?: string[];
  portal_greeting?: string;
  portal_background?: string;
  portal_cards?: PortalCard[];
  portal_card_opacity?: number;
  portal_logo_url?: string;
  app_name?: string;
  app_url?: string;
  logo_url?: string;
  inbound_email_domain?: string;
  ticket_form_settings?: TicketFormSettings;
}

export interface PortalCard {
  id: string;
  title: string;
  description: string;
  icon: string;
  action: 'create_ticket' | 'my_tickets' | 'kb' | 'chat' | 'url' | 'status';
  url?: string;
  default_team_id?: number;
  enabled: boolean;
  sort_order: number;
}

export const DEFAULT_PORTAL_CARDS: PortalCard[] = [
  { id: 'report-issue', title: 'Report an Issue', description: 'Something broken? Let us know and we\'ll get it sorted.', icon: 'alert-circle', action: 'create_ticket', enabled: true, sort_order: 0 },
  { id: 'check-status', title: 'Check Ticket Status', description: 'View and track your existing support requests.', icon: 'search', action: 'my_tickets', enabled: true, sort_order: 1 },
  { id: 'browse-kb', title: 'Browse Help Articles', description: 'Find answers in our knowledge base.', icon: 'book', action: 'kb', enabled: true, sort_order: 2 },
  { id: 'system-status', title: 'System Status', description: 'Check for planned outages or known issues.', icon: 'activity', action: 'status', enabled: true, sort_order: 3 },
];

export const BACKGROUND_PRESETS = [
  { id: 'gradient-indigo', label: 'Indigo' },
  { id: 'gradient-sunset', label: 'Sunset' },
  { id: 'gradient-emerald', label: 'Emerald' },
  { id: 'gradient-purple', label: 'Purple' },
  { id: 'gradient-dark', label: 'Dark' },
] as const;

export interface AppUser {
  id: number;
  name: string;
  email: string;
  role: 'super_admin' | 'tenant_admin' | 'agent' | 'end_user';
  tenant_id: number | null;
  permissions?: string[];
}

// RBAC
export type CustomFieldType = 'text' | 'textarea' | 'number' | 'select' | 'multi_select' | 'checkbox' | 'date' | 'url';
export type TicketTypeSlug = 'support' | 'task' | 'bug' | 'feature' | 'custom';

export interface CustomFieldOption {
  label: string;
  value: string;
}

export interface CustomFieldDefinition {
  id: number;
  tenant_id: number;
  category_id?: number | null;
  name: string;
  description?: string;
  field_key: string;
  field_type: CustomFieldType;
  options: CustomFieldOption[];
  applies_to: TicketTypeSlug[];
  is_required: boolean;
  is_required_to_create: boolean;
  is_required_to_close: boolean;
  is_customer_facing: boolean;
  is_agent_facing: boolean;
  sort_order: number;
  is_active: boolean;
  /** Populated by GET /api/tickets/:id — the current saved value for this ticket */
  current_value?: any;
  /** Nested field support */
  parent_field_id?: number | null;
  show_when?: { value?: string; values?: string[] } | null;
  nesting_depth?: number;
  created_at: string;
  updated_at: string;
}

export interface FormTemplate {
  id: number;
  tenant_id: number;
  name: string;
  description?: string;
  icon?: string;
  catalog_category?: string;
  ticket_type: TicketTypeSlug;
  field_ids: number[];
  default_category_id?: number | null;
  default_priority?: string | null;
  is_active: boolean;
  is_customer_facing: boolean;
  sort_order: number;
  fields?: CustomFieldDefinition[];
  created_at: string;
  updated_at: string;
}

export interface Permission {
  id: number;
  slug: string;
  label: string;
  category: string;
  description: string;
}

export interface Group {
  id: number;
  tenant_id: number;
  name: string;
  description: string;
  is_default: boolean;
  is_active: boolean;
  member_count: number;
  tenant_name?: string;
  created_at: string;
}

export interface UserPermissionOverride {
  slug: string;
  label: string;
  granted: boolean;
  reason: string;
}

// Tickets
export interface Ticket {
  id: number;
  tenant_id: number;
  ticket_number: string;
  subject: string;
  description: string;
  status: TicketStatus;
  priority: TicketPriority;
  category: string | null;
  tags: string[];
  requester_id: number;
  requester_name: string;
  requester_email: string | null;
  assignee_id: number | null;
  assignee_name: string | null;
  assignee_email: string | null;
  location_id: number | null;
  location_name: string | null;
  location_breadcrumb: string | null;
  problem_category_id: number | null;
  problem_category_name: string | null;
  problem_category_breadcrumb: string | null;
  sla_due_at: string | null;
  sla_first_response_due: string | null;
  sla_breached: boolean;
  sla_status: SlaStatus;
  age_seconds: number;
  first_response_at: string | null;
  last_responder: string | null;
  source: string;
  resolved_at: string | null;
  closed_at: string | null;
  parent_ticket_id: number | null;
  ticket_type: string;
  story_points: number | null;
  sprint_id: number | null;
  sprint_name: string | null;
  work_item_type_id: number | null;
  work_item_number: string | null;
  acceptance_criteria: string | null;
  parent_id: number | null;
  sort_order: number | null;
  created_at: string;
  updated_at: string;
}

export type TicketStatus = 'open' | 'pending' | 'resolved' | 'closed_not_resolved';

export type TicketPriority = 'p1' | 'p2' | 'p3' | 'p4';

export type SlaStatus = 'breached' | 'at_risk' | 'on_track' | 'no_sla';

export type TicketSortField = 'created_at' | 'updated_at' | 'sla_due_at' | 'priority' | 'priority_age';

export const SLA_STATUS_OPTIONS: { value: SlaStatus; label: string }[] = [
  { value: 'breached', label: 'Breached' },
  { value: 'at_risk', label: 'At Risk' },
  { value: 'on_track', label: 'On Track' },
  { value: 'no_sla', label: 'No SLA' },
];

export interface Agent {
  id: number;
  name: string;
  email: string;
  role: string;
}

export const STATUS_OPTIONS: { value: TicketStatus; label: string }[] = [
  { value: 'open', label: 'Open' },
  { value: 'pending', label: 'Pending' },
  { value: 'resolved', label: 'Resolved' },
  { value: 'closed_not_resolved', label: 'Closed (Not Resolved)' },
];

export const PRIORITY_OPTIONS: { value: TicketPriority; label: string }[] = [
  { value: 'p1', label: 'P1 — Urgent' },
  { value: 'p2', label: 'P2 — High' },
  { value: 'p3', label: 'P3 — Medium' },
  { value: 'p4', label: 'P4 — Low' },
];

export interface TicketComment {
  id: number;
  ticket_id: number;
  author_id: number;
  author_name: string;
  content: string;
  is_internal: boolean;
  is_ai_generated: boolean;
  created_at: string;
}

export interface TagSuggestion {
  id: number;
  ticket_id: number;
  tag: string;
  confidence: number | null;
  accepted: boolean | null;
  created_at: string;
}

// Hierarchies
export interface LocationNode {
  id: number;
  tenant_id: number;
  parent_id: number | null;
  name: string;
  level_label: string | null;
  sort_order: number;
  is_active: boolean;
  created_at: string;
}

export interface ProblemCategory {
  id: number;
  tenant_id: number;
  parent_id: number | null;
  name: string;
  sort_order: number;
  default_priority: string | null;
  team_id: number | null;
  is_active: boolean;
  created_at: string;
}

export interface AdminUser {
  id: number;
  tenant_id: number | null;
  email: string;
  name: string;
  first_name: string | null;
  last_name: string | null;
  phone: string | null;
  role: string;
  invite_status: 'invited' | 'active' | 'expired' | 'revoked';
  invited_at: string | null;
  expires_at: string | null;
  is_active: boolean;
  tenant_name: string | null;
  created_at: string;
}

export interface LevelConfig {
  column: string;
  fixed: string;
}

export interface LocationDbSyncConfig {
  id: number;
  db_type: string;
  host: string;
  port: number;
  dbname: string;
  db_user: string;
  schema: string;
  table: string;
  levels: {
    company: LevelConfig;
    country: LevelConfig;
    state:   LevelConfig;
    city:    LevelConfig;
    store:   LevelConfig;
  };
  preview_columns: string[];
  webhook_token: string;
  last_sync_at: string | null;
  last_error: string | null;
  last_result: { created: number; skipped: number; linked: number; total_fetched: number } | null;
}

export interface CategoryDbSyncConfig {
  id: number;
  db_type: string;
  host: string;
  port: number;
  dbname: string;
  db_user: string;
  schema: string;
  table: string;
  tier1_column: string;
  tier2_column: string;
  tier3_column: string;
  tier4_column: string;
  severity_column: string;
  preview_columns: string[];
  webhook_token: string;
  last_sync_at: string | null;
  last_error: string | null;
  last_result: { created: number; skipped: number; total_fetched: number } | null;
}

// Knowledge Base
export interface KnowledgeModule {
  id: number;
  slug: string;
  name: string;
  description: string;
  icon: string;
  module_type: 'knowledge' | 'feature';
  doc_count: number;
  is_active: boolean;
  enabled?: boolean;
  enabled_at?: string;
}

export interface Document {
  id: number;
  module_id: number;
  module_name: string;
  module_slug: string;
  title: string;
  source_url: string;
  tags: string[];
  created_at: string;
}

export interface DocumentTag {
  tag: string;
  count: number;
}

export interface SuggestedArticle {
  id: number;
  title: string;
  tags: string[];
  source_url: string;
  module_name: string;
  module_slug: string;
}

// Tenants
export interface Tenant {
  id: number;
  name: string;
  slug: string;
  domain: string | null;
  logo_url: string | null;
  settings: Record<string, unknown>;
  is_active: boolean;
  enabled_modules: number;
  ticket_prefix: string | null;
  created_at: string;
}

// AI Chat
export type MessageFeedback = 'positive' | 'negative' | null;

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  timestamp?: string;
  sources?: ChatSource[];
  feedback?: MessageFeedback;
}

export interface ChatSource {
  title: string;
  url: string;
  document_id: number;
  module: string;
}

export interface ChatStreamEvent {
  type: 'status' | 'text' | 'sources' | 'done' | 'conversation_id' | 'escalation' | 'resolved';
  content?: string;
  sources?: ChatSource[];
  modules_used?: string[];
  tokens?: number;
  conversation_id?: number;
  ticket_id?: number;
}

export interface ArticleRecommendation {
  id: number;
  document_id: number;
  turn_number: number;
  user_helpful: boolean | null;
  rated_at: string | null;
  title: string;
  url: string | null;
  module_id: number;
}

export interface AIConversation {
  id: number;
  tenant_id: number;
  ticket_id: number | null;
  language: string;
  channel: 'text' | 'voice';
  messages: ChatMessage[];
  modules_used: string[];
  tokens_used: number;
  feedback?: Array<{ message_index: number; rating: string; comment?: string }>;
  first_message?: string;
  status?: 'active' | 'archived';
  updated_at?: string;
  turn_count?: number;
  created_at: string;
  ticket_status?: string | null;
  ticket_number?: string | null;
}

// Module features (sub-toggles)
export interface ModuleFeature {
  id: number;
  module_id: number;
  slug: string;
  name: string;
  description: string;
  icon: string;
  sort_order: number;
  enabled: boolean;
  enabled_at?: string;
  enabled_by?: string;
}

// Audit Queue
export interface AuditQueueItem {
  id: number;
  ticket_id: number;
  tenant_id: number;
  ticket_number: string;
  subject: string;
  ticket_status: string;
  priority: string;
  queue_type: 'auto_resolved' | 'human_resolved' | 'low_confidence' | 'kba_candidate';
  status: 'pending' | 'reviewed' | 'approved' | 'dismissed' | 'auto_closed' | 'auto_approved' | 'auto_dismissed';
  ai_suggested_tags: string[];
  current_tags: string[];
  current_category_name: string | null;
  suggested_category_name: string | null;
  ai_category_confidence: number | null;
  resolution_score: number | null;
  resolution_notes: string | null;
  kba_draft: string | null;
  reviewed_by_name: string | null;
  reviewed_at: string | null;
  auto_close_at: string | null;
  created_at: string;
}

export interface AuditStats {
  total_pending: number;
  auto_resolved: number;
  human_resolved: number;
  low_confidence: number;
  kba_candidates: number;
  avg_resolution_score: number | null;
}

// Ticket metrics
export interface TicketMetrics {
  ticket_id: number;
  effort_score: number | null;
  reply_count: number;
  requester_replies: number;
  agent_replies: number;
  resolved_first_contact: boolean | null;
  escalation_count: number;
  suggested_assignee_id: number | null;
  suggested_assignee_name: string | null;
  routing_confidence: number | null;
  routing_reason: string | null;
}

// Knowledge gaps
export interface KnowledgeGap {
  id: number;
  tenant_id: number;
  topic: string;
  ticket_count: number;
  sample_tickets: number[];
  suggested_title: string;
  status: 'detected' | 'acknowledged' | 'article_created' | 'dismissed';
  article_title: string | null;
  detected_at: string;
  updated_at: string;
}

// Metrics summary
export interface MetricsSummary {
  total_tickets: number;
  avg_effort_score: number | null;
  fcr_count: number;
  fcr_total: number;
  fcr_rate: number | null;
  avg_replies: number | null;
  avg_escalations: number | null;
}

// Pipeline Queue
export interface QueueStats {
  queue_depth: number;
  running: number;
  running_llm: number;
  failed_total: number;
  completed_last_hour: number;
  failed_last_hour: number;
  avg_duration_ms: number;
  oldest_pending_age_seconds: number | null;
}

export interface QueueTask {
  id: number;
  ticket_id: number | null;
  ticket_number?: string;
  step_name: string;
  status: string;
  priority: number;
  uses_llm: boolean;
  attempts: number;
  max_attempts: number;
  last_error: string | null;
  duration_ms: number | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface PipelineExecution {
  id: number;
  queue_id: number | null;
  ticket_id: number | null;
  ticket_number?: string;
  tenant_id?: number | null;
  step_name: string;
  status: string;
  error_message: string | null;
  output_summary: string | null;
  duration_ms: number | null;
  attempts: number;
  created_at: string;
}

export interface PipelineSchedule {
  id: number;
  step_name: string;
  cron_expression: string;
  enabled: boolean;
  last_enqueued_at: string | null;
  created_at: string;
}

// ── Automations ─────────────────────────────────────────

export type TriggerType = 'ticket_created' | 'status_changed' | 'priority_changed' | 'comment_added' | 'assignee_changed' | 'tag_added' | 'sla_breached' | 'schedule';

export interface Automation {
  id: number;
  tenant_id: number;
  name: string;
  description: string;
  trigger_type: TriggerType;
  trigger_config: Record<string, any>;
  is_active: boolean;
  created_by: number | null;
  created_by_name?: string;
  updated_by: number | null;
  created_at: string;
  updated_at: string;
  run_count: number;
  last_run_at: string | null;
  nodes?: AutomationNode[];
  edges?: AutomationEdge[];
}

export interface AutomationNode {
  id: string;
  automation_id: number;
  node_type: 'trigger' | 'condition' | 'action';
  node_subtype: string;
  position_x: number;
  position_y: number;
  config: Record<string, any>;
  label: string;
}

export interface AutomationEdge {
  id: string;
  automation_id: number;
  source_node: string;
  target_node: string;
  source_handle: string;
}

export interface AutomationRun {
  id: number;
  automation_id: number;
  ticket_id: number | null;
  tenant_id: number;
  status: 'running' | 'completed' | 'failed' | 'skipped';
  trigger_type: string;
  started_at: string;
  completed_at: string | null;
  duration_ms: number | null;
  nodes_executed: number;
  actions_taken: Array<{ node_id: string; type: string; subtype: string; result: string }>;
  error: string | null;
  ticket_snapshot: Record<string, any>;
  automation_name?: string;
  ticket_number?: string;
  ticket_subject?: string;
}

// ── Notification group × event matrix ───────────────────

export interface GroupEventSubscription {
  event: string;
  channel: string;
  enabled: boolean;
}

export interface GroupEventMatrixEntry {
  group_id: number;
  group_name: string;
  events: GroupEventSubscription[];
}

export interface TeamEventMatrixEntry {
  team_id: number;
  team_name: string;
  events: GroupEventSubscription[];
}

export interface NotificationTemplate {
  event: string;
  is_custom: boolean;
  subject_template: string;
  body_headline: string;
  body_intro: string;
  default_subject: string;
  default_headline: string;
  default_intro: string;
  variables: string[];
}

export interface PhoneConfig {
  configured: boolean;
  id?: number;
  is_active: boolean;
  credentials_mode?: 'platform' | 'byok';
  assigned_phone_number?: string;
  effective_phone_number?: string;
  elevenlabs_agent_id?: string;
  elevenlabs_phone_number_id?: string;
  voice_id: string;
  agent_name: string;
  greeting_message?: string;
  oncall_number?: string;
  // IVR greeting customization
  ivr_greeting_en?: string;
  ivr_greeting_es?: string;
  // AI & Voice fields (nullable = use backend default)
  tts_speed?: number | null;
  llm_model?: string | null;
  temperature?: number | null;
  turn_timeout?: number | null;
  audio_format?: string | null;
  // BYOK-only fields
  twilio_account_sid?: string;
  twilio_phone_number?: string;
  elevenlabs_api_key_set?: boolean;
  twilio_auth_token_set?: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface PhoneSession {
  id: number;
  elevenlabs_conversation_id?: string;
  caller_phone?: string;
  caller_email?: string;
  status: string;
  transfer_attempted: boolean;
  transfer_succeeded: boolean;
  ticket_id?: number;
  ticket_number?: string;
  duration_seconds?: number;
  started_at: string;
  ended_at?: string;
  summary?: string;
  el_cost_credits?: number;
  el_llm_input_tokens?: number;
  el_llm_output_tokens?: number;
  twilio_cost_cents?: number;
  agent_name?: string;
  agent_slug?: string;
}

export interface PhoneAgent {
  id: number;
  tenant_id: number;
  slug: string;
  name: string;
  language: string;
  el_agent_id?: string;
  voice_id?: string;
  greeting_message?: string;
  ivr_greeting?: string;
  system_prompt?: string;
  has_custom_prompt?: boolean;
  llm_model?: string | null;
  temperature?: number | null;
  turn_timeout?: number | null;
  audio_format?: string | null;
  tts_speed?: number | null;
  ivr_digit?: string;
  oncall_number?: string;
  is_active: boolean;
  is_deployed: boolean;
  is_number_linked: boolean;
  tools_enabled?: string[];
  sort_order: number;
  created_at?: string;
  updated_at?: string;
}

// ─── Messaging (SMS + WhatsApp) ───

export interface MessagingConfig {
  configured: boolean;
  sms_enabled: boolean;
  whatsapp_enabled: boolean;
  whatsapp_phone_number?: string | null;
  whatsapp_status: 'not_configured' | 'sandbox' | 'pending' | 'approved';
  auto_reply_enabled: boolean;
  auto_reply_message: string;
  auto_create_ticket: boolean;
  default_language: string;
  sms_phone_number?: string | null;
  credentials_mode?: 'platform' | 'byok';
}

export interface MessagingConversation {
  id: number;
  tenant_id: number;
  channel: 'sms' | 'whatsapp';
  contact_phone: string;
  contact_name?: string | null;
  contact_email?: string | null;
  user_id?: number | null;
  language: string;
  ticket_id?: number | null;
  status: 'active' | 'resolved' | 'archived';
  last_message_at?: string | null;
  last_inbound_at?: string | null;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: number;
  conversation_id: number;
  tenant_id: number;
  direction: 'inbound' | 'outbound';
  channel: 'sms' | 'whatsapp';
  body?: string | null;
  media_url?: string | null;
  twilio_message_sid?: string | null;
  status: 'queued' | 'sent' | 'delivered' | 'read' | 'failed' | 'received';
  error_code?: string | null;
  error_message?: string | null;
  segments: number;
  cost_cents?: number | null;
  language?: string | null;
  template_name?: string | null;
  sender_user_id?: number | null;
  created_at: string;
}

export interface MessagingTemplate {
  id: number;
  tenant_id: number;
  name: string;
  language: string;
  body: string;
  category: 'utility' | 'marketing' | 'authentication';
  status: 'draft' | 'pending' | 'approved' | 'rejected';
  twilio_template_sid?: string | null;
  variables: any[];
  created_at: string;
  updated_at: string;
}

export interface MessagingStats {
  active_conversations: number;
  total_conversations: number;
  inbound_30d: number;
  outbound_30d: number;
  total_cost_cents_30d: number;
  total_segments_30d: number;
}
