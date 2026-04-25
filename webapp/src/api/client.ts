/**
 * API client — thin fetch wrapper with error handling.
 */

import type { LocationNode, ProblemCategory, TagSuggestion, Agent, ChatStreamEvent, AIConversation, ArticleRecommendation, LocationDbSyncConfig, CategoryDbSyncConfig, ModuleFeature, AuditQueueItem, AuditStats, TicketMetrics, KnowledgeGap, MetricsSummary } from '../types';

const BASE = '/api';

class ApiError extends Error {
  status: number;
  body: any;
  constructor(message: string, status: number, body: any) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

function getCsrfHeaders(): Record<string, string> {
  const token = window.__APP_CONFIG__?.csrf_token;
  return token ? { 'X-CSRF-Token': token } : {};
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...getCsrfHeaders(), ...options?.headers },
    ...options,
  });
  // Safety net: if fetch followed a redirect to the login page, redirect immediately
  if (res.redirected && res.url.includes('/login')) {
    window.location.href = '/login?reason=timeout';
    return new Promise(() => {});
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: res.statusText }));
    // Global session intercept: redirect to login on any auth failure
    if (res.status === 401 && (body?.code === 'session_timeout' || body?.code === 'session_expired')) {
      window.location.href = '/login?reason=timeout';
      // Return a never-resolving promise so callers don't process a partial response
      return new Promise(() => {});
    }
    throw new ApiError(body.error || `HTTP ${res.status}`, res.status, body);
  }
  return res.json();
}

export const api = {
  // Generic request for ad-hoc endpoints
  request: <T = any>(method: string, path: string, body?: any) =>
    request<T>(path, {
      method,
      ...(body ? { body: JSON.stringify(body) } : {}),
    }),

  // Tickets
  listTickets: (params?: Record<string, string>) => {
    const qs = params ? '?' + new URLSearchParams(params).toString() : '';
    return request<{ tickets: any[]; total: number }>(`/tickets${qs}`);
  },
  getTicket: (id: number) => request<{ ticket: any; comments: any[]; tag_suggestions: TagSuggestion[] }>(`/tickets/${id}`),
  createTicket: (data: any) => request<{ id: number; ticket_number: string }>('/tickets', { method: 'POST', body: JSON.stringify(data) }),
  updateTicket: (id: number, data: any) => request<{ ok: boolean }>(`/tickets/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  addComment: (ticketId: number, data: { content: string; is_internal?: boolean; send_email?: boolean; cc?: string[]; attachment_ids?: number[] }) =>
    request<{ id: number }>(`/tickets/${ticketId}/comments`, { method: 'POST', body: JSON.stringify(data) }),

  // File attachments
  uploadAttachment: async (ticketId: number, file: File) => {
    const form = new FormData();
    form.append('file', file);
    const res = await fetch(`${BASE}/tickets/${ticketId}/attachments`, { method: 'POST', body: form, headers: getCsrfHeaders() });
    if (!res.ok) {
      const body = await res.json().catch(() => ({ error: res.statusText }));
      throw new Error(body.error || `HTTP ${res.status}`);
    }
    return res.json() as Promise<{ id: number; filename: string; file_size: number; content_type: string }>;
  },
  listAgents: () => request<Agent[]>('/tickets/agents'),
  listAllUsers: () => request<Agent[]>('/tickets/agents?include_end_users=true'),

  // Tag suggestions
  acceptTag: (ticketId: number, suggestionId: number, accepted: boolean) =>
    request<{ ok: boolean }>(`/tickets/${ticketId}/tags/accept`, { method: 'POST', body: JSON.stringify({ suggestion_id: suggestionId, accepted }) }),
  addTag: (ticketId: number, tag: string) =>
    request<{ ok: boolean }>(`/tickets/${ticketId}/tags`, { method: 'POST', body: JSON.stringify({ tag }) }),

  // Custom Fields
  listCustomFields: (params?: { include_inactive?: boolean }) => {
    const qs = params?.include_inactive ? '?include_inactive=true' : '';
    return request<{ fields: any[] }>(`/custom-fields${qs}`);
  },
  createCustomField: (data: any) => request<{ field: any }>('/custom-fields', { method: 'POST', body: JSON.stringify(data) }),
  updateCustomField: (id: number, data: any) => request<{ field: any }>(`/custom-fields/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteCustomField: (id: number) => request<{ ok: boolean }>(`/custom-fields/${id}`, { method: 'DELETE' }),
  reorderCustomFields: (order: number[]) => request<{ ok: boolean }>('/custom-fields/reorder', { method: 'PUT', body: JSON.stringify({ order }) }),
  listCustomFieldsForForm: (params: { category_id?: number; ticket_type?: string; form_template_id?: number }) => {
    const qs = new URLSearchParams();
    if (params.category_id) qs.set('category_id', String(params.category_id));
    if (params.ticket_type) qs.set('ticket_type', params.ticket_type);
    if (params.form_template_id) qs.set('form_template_id', String(params.form_template_id));
    return request<{ fields: any[] }>(`/custom-fields/for-form?${qs}`);
  },

  // Hierarchies
  listLocations: () => request<LocationNode[]>('/hierarchies/locations'),
  createLocation: (data: any) => request<{ id: number }>('/hierarchies/locations', { method: 'POST', body: JSON.stringify(data) }),
  updateLocation: (id: number, data: any) => request<{ ok: boolean }>(`/hierarchies/locations/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteLocation: (id: number) => request<{ ok: boolean }>(`/hierarchies/locations/${id}`, { method: 'DELETE' }),

  importLocations: async (file: File) => {
    const form = new FormData();
    form.append('file', file);
    const res = await fetch(`${BASE}/hierarchies/locations/import`, { method: 'POST', body: form, headers: getCsrfHeaders() });
    if (!res.ok) {
      const body = await res.json().catch(() => ({ error: res.statusText }));
      throw new Error(body.error || `HTTP ${res.status}`);
    }
    return res.json() as Promise<{ created: number; skipped: number; linked: number }>;
  },

  // Location DB Sync
  getLocationDbSync: () => request<LocationDbSyncConfig | null>('/hierarchies/locations/db-sync'),
  saveLocationDbSync: (data: { db_type?: string; host?: string; port?: number; dbname?: string; db_user?: string; password?: string; schema?: string; table: string; levels: Record<string, { column: string; fixed: string }>; preview_columns?: string[] }) =>
    request<{ ok: boolean; webhook_token: string }>('/hierarchies/locations/db-sync', { method: 'POST', body: JSON.stringify(data) }),
  testLocationDbSync: (data: { db_type?: string; host?: string; port?: number; dbname?: string; db_user?: string; password?: string; schema?: string; table: string }) =>
    request<{ columns: string[]; rows: Record<string, string | null>[] }>('/hierarchies/locations/db-sync/test', { method: 'POST', body: JSON.stringify(data) }),
  runLocationDbSync: async (webhookToken: string) => {
    const res = await fetch(`${BASE}/hierarchies/locations/db-sync/run`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${webhookToken}`, ...getCsrfHeaders() },
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({ error: res.statusText }));
      throw new Error(body.error || `HTTP ${res.status}`);
    }
    return res.json() as Promise<{ created: number; skipped: number; linked: number; total_fetched: number }>;
  },

  // Category DB Sync
  getCategoryDbSync: () => request<CategoryDbSyncConfig | null>('/hierarchies/problem-categories/db-sync'),
  saveCategoryDbSync: (data: { db_type?: string; host?: string; port?: number; dbname?: string; db_user?: string; password?: string; schema?: string; table: string; tier1_column?: string; tier2_column?: string; tier3_column?: string; tier4_column?: string; severity_column?: string; preview_columns?: string[] }) =>
    request<{ ok: boolean; webhook_token: string }>('/hierarchies/problem-categories/db-sync', { method: 'POST', body: JSON.stringify(data) }),
  testCategoryDbSync: (data: { db_type?: string; host?: string; port?: number; dbname?: string; db_user?: string; password?: string; schema?: string; table: string }) =>
    request<{ columns: string[]; rows: Record<string, string | null>[] }>('/hierarchies/problem-categories/db-sync/test', { method: 'POST', body: JSON.stringify(data) }),
  runCategoryDbSync: async (webhookToken: string) => {
    const res = await fetch(`${BASE}/hierarchies/problem-categories/db-sync/run`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${webhookToken}`, ...getCsrfHeaders() },
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({ error: res.statusText }));
      throw new Error(body.error || `HTTP ${res.status}`);
    }
    return res.json() as Promise<{ created: number; skipped: number; total_fetched: number }>;
  },

  listProblemCategories: () => request<ProblemCategory[]>('/hierarchies/problem-categories'),
  createProblemCategory: (data: any) => request<{ id: number }>('/hierarchies/problem-categories', { method: 'POST', body: JSON.stringify(data) }),
  updateProblemCategory: (id: number, data: any) => request<{ ok: boolean }>(`/hierarchies/problem-categories/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteProblemCategory: (id: number) => request<{ ok: boolean }>(`/hierarchies/problem-categories/${id}`, { method: 'DELETE' }),
  importProblemCategories: async (file: File) => {
    const form = new FormData();
    form.append('file', file);
    const res = await fetch(`${BASE}/hierarchies/problem-categories/import`, { method: 'POST', body: form, headers: getCsrfHeaders() });
    if (!res.ok) {
      const body = await res.json().catch(() => ({ error: res.statusText }));
      throw new Error(body.error || `HTTP ${res.status}`);
    }
    return res.json() as Promise<{ created: number; skipped: number }>;
  },

  // Knowledge Base
  listKbModules: () => request<any[]>('/kb/modules'),
  listDocuments: (params?: Record<string, string>) => {
    const qs = params ? '?' + new URLSearchParams(params).toString() : '';
    return request<{ documents: any[]; total: number }>(`/kb/documents${qs}`);
  },
  getDocument: (id: number) => request<any>(`/kb/documents/${id}`),
  listDocumentTags: () => request<{ tag: string; count: number }[]>('/kb/tags'),
  suggestArticles: (ticketId: number) => request<any[]>(`/kb/suggest/${ticketId}`),
  sendArticleToTicket: (documentId: number, ticketId: number) =>
    request<{ id: number }>('/kb/send-to-ticket', { method: 'POST', body: JSON.stringify({ document_id: documentId, ticket_id: ticketId }) }),
  sendChatResponseToTicket: (content: string, ticketId: number, isInternal?: boolean) =>
    request<{ id: number }>('/ai/send-to-ticket', { method: 'POST', body: JSON.stringify({ content, ticket_id: ticketId, is_internal: isInternal ?? false }) }),

  // KB Collections (tenant-scoped groupings)
  listCollections: () => request<any[]>('/kb/collections'),
  createCollection: (data: { name: string; description?: string }) =>
    request<{ id: number; name: string; slug: string }>('/kb/collections', { method: 'POST', body: JSON.stringify(data) }),
  deleteCollection: (id: number) =>
    request<{ ok: boolean }>(`/kb/collections/${id}`, { method: 'DELETE' }),

  // KB Articles (tenant-created)
  listArticles: (collectionSlug?: string) => {
    const qs = collectionSlug ? `?collection=${encodeURIComponent(collectionSlug)}` : '';
    return request<any[]>(`/kb/articles${qs}`);
  },
  createArticle: (data: { title: string; content: string; is_published?: boolean; collection_id?: number }) =>
    request<{ id: number }>('/kb/articles', { method: 'POST', body: JSON.stringify(data) }),
  getArticle: (id: number) => request<any>(`/kb/articles/${id}`),
  updateArticle: (id: number, data: { title?: string; content?: string; is_published?: boolean; tenant_collection_id?: number | null }) =>
    request<{ ok: boolean }>(`/kb/articles/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteArticle: (id: number) =>
    request<{ ok: boolean }>(`/kb/articles/${id}`, { method: 'DELETE' }),
  listUploadHistory: (collectionId?: number) => {
    const qs = collectionId ? `?collection_id=${collectionId}` : '';
    return request<any[]>(`/kb/upload-history${qs}`);
  },

  uploadArticles: async (collectionSlug: string, files: File[]): Promise<{ collection_id: number; uploaded: number; errors: number; results: any[] }> => {
    const form = new FormData();
    form.append('collection', collectionSlug);
    for (const f of files) form.append('files', f);
    const res = await fetch(`${BASE}/kb/articles/upload`, { method: 'POST', body: form, headers: getCsrfHeaders() });
    if (!res.ok) {
      const body = await res.json().catch(() => ({ error: res.statusText }));
      throw new Error(body.error || `HTTP ${res.status}`);
    }
    return res.json();
  },

  // AI Chat
  chatStream: (
    data: { query: string; conversation_id?: number; language?: string; ticket_id?: number },
    onEvent: (event: ChatStreamEvent) => void,
    onError?: (error: Error) => void,
  ): AbortController => {
    const controller = new AbortController();

    fetch(`${BASE}/ai/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getCsrfHeaders() },
      body: JSON.stringify({ ...data, stream: true }),
      signal: controller.signal,
    })
      .then(async (res) => {
        if (!res.ok) {
          const body = await res.json().catch(() => ({ error: res.statusText }));
          throw new Error(body.error || `HTTP ${res.status}`);
        }

        const reader = res.body?.getReader();
        if (!reader) throw new Error('No response body');

        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try {
                const event: ChatStreamEvent = JSON.parse(line.slice(6));
                onEvent(event);
              } catch { /* skip malformed */ }
            }
          }
        }
      })
      .catch((err) => {
        if (err.name !== 'AbortError') {
          onError?.(err);
        }
      });

    return controller;
  },

  listConversations: () => request<AIConversation[]>('/ai/conversations'),
  getConversation: (id: number) => request<AIConversation>(`/ai/conversations/${id}`),
  getConversationByTicket: (ticketId: number) => request<AIConversation | null>(`/ai/conversations/by-ticket/${ticketId}`),
  archiveConversation: (conversationId: number) =>
    request<{ ok: boolean }>(`/ai/conversations/${conversationId}/archive`, { method: 'POST' }),
  submitFeedback: (conversationId: number, messageIndex: number, rating: 'positive' | 'negative', comment?: string) =>
    request<{ ok: boolean }>(`/ai/conversations/${conversationId}/feedback`, {
      method: 'POST',
      body: JSON.stringify({ message_index: messageIndex, rating, comment }),
    }),
  getConversationArticles: (conversationId: number) =>
    request<ArticleRecommendation[]>(`/ai/conversations/${conversationId}/articles`),
  rateArticle: (recId: number, helpful: boolean | null) =>
    request<{ ok: boolean }>(`/ai/article-recommendations/${recId}/rate`, {
      method: 'POST',
      body: JSON.stringify({ helpful }),
    }),
  // Chat-to-case
  chatToCase: (data: { conversation_id: number; subject: string; transcript: string }) =>
    request<{ ticket_id: number; ticket_number: string }>('/ai/chat-to-case', { method: 'POST', body: JSON.stringify(data) }),
  chatToCaseAppend: (ticketId: number, content: string, role: 'user' | 'assistant') =>
    request<{ ok: boolean }>(`/ai/chat-to-case/${ticketId}/append`, { method: 'POST', body: JSON.stringify({ content, role }) }),

  // Admin
  getUsageStats: (params?: { period?: string; tenant_id?: number; start_date?: string; end_date?: string }) => {
    const qs = params ? '?' + new URLSearchParams(
      Object.fromEntries(Object.entries(params).filter(([, v]) => v != null).map(([k, v]) => [k, String(v)]))
    ).toString() : '';
    return request<any>(`/admin/usage${qs}`);
  },
  listTenants: () => request<any[]>('/admin/tenants'),
  updateTenantSettings: (tenantId: number, settings: any) =>
    request<{ ok: boolean }>(`/admin/tenants/${tenantId}/settings`, { method: 'PUT', body: JSON.stringify(settings) }),
  getAllowedDomains: (tenantId: number) =>
    request<{ allowed_domains: string }>(`/admin/tenants/${tenantId}/allowed-domains`),
  getTenantModules: (tenantId: number) => request<any[]>(`/admin/tenants/${tenantId}/modules`),
  enableModule: (tenantId: number, moduleId: number) => request<{ ok: boolean }>(`/admin/tenants/${tenantId}/modules/${moduleId}/enable`, { method: 'POST' }),
  disableModule: (tenantId: number, moduleId: number) => request<{ ok: boolean }>(`/admin/tenants/${tenantId}/modules/${moduleId}/disable`, { method: 'POST' }),
  listUsers: () => request<any[]>('/admin/users'),
  createUser: (data: any) => request<{ id: number }>('/admin/users', { method: 'POST', body: JSON.stringify(data) }),
  updateUser: (id: number, data: any) => request<{ ok: boolean }>(`/admin/users/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  resendInvite: (userId: number) => request<{ ok: boolean }>(`/admin/users/${userId}/resend-invite`, { method: 'POST' }),
  bulkImportUsers: async (file: File, tenantId?: number) => {
    const form = new FormData();
    form.append('file', file);
    if (tenantId) form.append('tenant_id', String(tenantId));
    const res = await fetch(`${BASE}/admin/users/bulk-import`, { method: 'POST', body: form, headers: getCsrfHeaders() });
    if (!res.ok) {
      const body = await res.json().catch(() => ({ error: res.statusText }));
      throw new Error(body.error || `HTTP ${res.status}`);
    }
    return res.json() as Promise<{ created: number; skipped: number; errors: string[] }>;
  },
  exportUsers: async (tenantId?: number) => {
    const qs = tenantId ? `?tenant_id=${tenantId}` : '';
    const res = await fetch(`${BASE}/admin/users/export${qs}`);
    if (!res.ok) {
      const body = await res.json().catch(() => ({ error: res.statusText }));
      throw new Error(body.error || `HTTP ${res.status}`);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'users.csv';
    a.click();
    URL.revokeObjectURL(url);
  },

  // Module features
  getModuleFeatures: (tenantId: number, moduleId: number) =>
    request<ModuleFeature[]>(`/admin/tenants/${tenantId}/modules/${moduleId}/features`),
  enableFeature: (tenantId: number, featureId: number) =>
    request<{ ok: boolean }>(`/admin/tenants/${tenantId}/features/${featureId}/enable`, { method: 'POST' }),
  disableFeature: (tenantId: number, featureId: number) =>
    request<{ ok: boolean }>(`/admin/tenants/${tenantId}/features/${featureId}/disable`, { method: 'POST' }),

  // Teams
  listTeams: () => request<any[]>('/admin/teams'),
  createTeam: (data: { name: string; description?: string; lead_id?: number }) =>
    request<{ id: number; name: string; slug: string }>('/admin/teams', { method: 'POST', body: JSON.stringify(data) }),
  updateTeam: (id: number, data: { name?: string; description?: string; lead_id?: number | null; is_active?: boolean }) =>
    request<{ ok: boolean }>(`/admin/teams/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteTeam: (id: number) =>
    request<{ ok: boolean }>(`/admin/teams/${id}`, { method: 'DELETE' }),
  getTeamMembers: (id: number) => request<any[]>(`/admin/teams/${id}/members`),
  updateTeamMembers: (id: number, members: { user_id: number; role: string }[]) =>
    request<{ ok: boolean; count: number }>(`/admin/teams/${id}/members`, { method: 'PUT', body: JSON.stringify({ members }) }),

  // Sprints
  listSprints: (params?: { team_id?: number; status?: string }) => {
    const qs = params ? '?' + new URLSearchParams(
      Object.fromEntries(Object.entries(params).filter(([, v]) => v != null).map(([k, v]) => [k, String(v)]))
    ).toString() : '';
    return request<any[]>(`/sprints${qs}`);
  },
  createSprint: (data: { name: string; team_id: number; goal?: string; start_date?: string; end_date?: string }) =>
    request<{ id: number }>('/sprints', { method: 'POST', body: JSON.stringify(data) }),
  updateSprint: (id: number, data: { name?: string; goal?: string; start_date?: string; end_date?: string; status?: string }) =>
    request<{ ok: boolean }>(`/sprints/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteSprint: (id: number) =>
    request<{ ok: boolean }>(`/sprints/${id}`, { method: 'DELETE' }),
  getSprintBoard: (id: number) =>
    request<{ sprint: any; workflow: any[]; columns: Record<string, any[]>; total_points: number; completed_points: number }>(`/sprints/${id}/board`),
  getSprintBacklog: (id: number, params?: { team_only?: boolean; search?: string }) => {
    const qs = new URLSearchParams();
    if (params?.team_only) qs.set('team_only', 'true');
    if (params?.search) qs.set('search', params.search);
    const q = qs.toString();
    return request<any[]>(`/sprints/${id}/backlog${q ? '?' + q : ''}`);
  },
  addSprintItems: (id: number, ticket_ids: number[]) =>
    request<{ ok: boolean; count: number }>(`/sprints/${id}/items`, { method: 'POST', body: JSON.stringify({ ticket_ids }) }),
  removeSprintItem: (sprintId: number, ticketId: number) =>
    request<{ ok: boolean }>(`/sprints/${sprintId}/items/${ticketId}`, { method: 'DELETE' }),
  getSprintTimeline: (id: number) =>
    request<any[]>(`/sprints/${id}/timeline`),
  getVelocityAverages: (params?: { team_id?: number }) => {
    const qs = params?.team_id ? `?team_id=${params.team_id}` : '';
    return request<{ team_avg: number; sprint_count: number; person_averages: any[] }>(`/sprints/velocity/averages${qs}`);
  },
  listWorkItemTypes: () =>
    request<any[]>('/work-item-types'),
  createWorkItemType: (data: { name: string; slug?: string; icon?: string; color?: string }) =>
    request<{ id: number }>('/work-item-types', { method: 'POST', body: JSON.stringify(data) }),
  reorderSprintItems: (sprintId: number, ticketIds: number[]) =>
    request<{ ok: boolean }>(`/sprints/${sprintId}/reorder`, { method: 'PUT', body: JSON.stringify({ ticket_ids: ticketIds }) }),
  getSprintCapacity: (sprintId: number) =>
    request<any>(`/sprints/${sprintId}/capacity`),

  // Work item hierarchy
  getTicketChildren: (ticketId: number) =>
    request<any[]>(`/tickets/${ticketId}/children`),
  getTicketTree: (ticketId: number) =>
    request<any[]>(`/tickets/${ticketId}/tree`),
  getTicketRollup: (ticketId: number) =>
    request<any>(`/tickets/${ticketId}/rollup`),

  // Ticket activity timeline
  getTicketActivity: (ticketId: number) =>
    request<Array<{ id: number; activity_type: string; old_value: string | null; new_value: string | null; metadata: any; created_at: string; user_name: string | null }>>(
      `/tickets/${ticketId}/activity`
    ),

  // Subtask checklist
  listTicketTasks: (ticketId: number) =>
    request<any[]>(`/tickets/${ticketId}/tasks`),
  createTicketTask: (ticketId: number, data: { title: string }) =>
    request<{ id: number }>(`/tickets/${ticketId}/tasks`, { method: 'POST', body: JSON.stringify(data) }),
  updateTicketTask: (ticketId: number, taskId: number, data: any) =>
    request<{ ok: boolean }>(`/tickets/${ticketId}/tasks/${taskId}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteTicketTask: (ticketId: number, taskId: number) =>
    request<{ ok: boolean }>(`/tickets/${ticketId}/tasks/${taskId}`, { method: 'DELETE' }),

  // Ticket workflows
  getWorkflowStatuses: (ticketType: string) =>
    request<any[]>(`/tickets/workflows/${ticketType}`),

  // Audit queue
  listAuditQueue: (params?: Record<string, string>) => {
    const qs = params ? '?' + new URLSearchParams(params).toString() : '';
    return request<{ items: AuditQueueItem[]; total: number }>(`/audit/queue${qs}`);
  },
  reviewAuditItem: (itemId: number, action: 'approve' | 'dismiss') =>
    request<{ ok: boolean }>(`/audit/queue/${itemId}/review`, { method: 'POST', body: JSON.stringify({ action }) }),
  reopenAuditItem: (itemId: number) =>
    request<{ ok: boolean }>(`/audit/queue/${itemId}/reopen`, { method: 'POST' }),
  bulkManageQueue: (action: 'approve' | 'dismiss', itemIds?: number[], allPending?: boolean) =>
    request<{ ok: boolean }>('/audit/queue/bulk', { method: 'POST', body: JSON.stringify({ action, item_ids: itemIds, all_pending: allPending }) }),
  getAuditStats: () => request<AuditStats>('/audit/queue/stats'),
  getAuditSettings: () => request<any>('/audit/settings'),
  updateAuditSettings: (data: any) =>
    request<{ ok: boolean }>('/audit/settings', { method: 'PUT', body: JSON.stringify(data) }),

  // Knowledge gaps
  listKnowledgeGaps: (status?: string) => {
    const qs = status ? `?status=${status}` : '';
    return request<KnowledgeGap[]>(`/audit/knowledge-gaps${qs}`);
  },
  updateKnowledgeGap: (id: number, status: string) =>
    request<{ ok: boolean }>(`/audit/knowledge-gaps/${id}`, { method: 'PUT', body: JSON.stringify({ status }) }),
  detectKnowledgeGaps: () =>
    request<{ ok: boolean }>('/audit/knowledge-gaps/detect', { method: 'POST' }),

  // Ticket metrics
  getTicketMetrics: (ticketId: number) => request<TicketMetrics>(`/audit/metrics/ticket/${ticketId}`),
  getMetricsSummary: () => request<MetricsSummary>('/audit/metrics'),

  // Atlas insights for ticket detail
  getAtlasInsights: (ticketId: number) =>
    request<{
      routing: { suggested_assignee_name: string; confidence: number; reason: string } | null;
      category_suggestion: { category_name: string; confidence: number } | null;
      metrics: { resolution_score: number | null; effort_score: number | null; fcr: boolean | null } | null;
    }>(`/tickets/${ticketId}/atlas-insights`),

  // Atlas engagement
  getEngagementStatus: (ticketId: number) =>
    request<{
      status: string;
      engagement_type?: string;
      human_took_over?: boolean;
      resolved_by_ai?: boolean;
      kb_articles_referenced?: string[];
      suggested_category_id?: number;
      suggested_category_name?: string;
      category_confidence?: number;
      similar_tickets?: Array<{ id: number; ticket_number: string; subject: string; status: string; priority: string }>;
      created_at?: string;
    }>(`/ai/engagement/${ticketId}`),

  // Similar tickets (on-demand, not dependent on auto-engage)
  getSimilarTickets: (ticketId: number) =>
    request<Array<{ id: number; ticket_number: string; subject: string; status: string; priority: string; similarity?: number }>>(
      `/tickets/${ticketId}/similar`
    ),

  // Incident linking
  linkIncident: (ticketId: number, parentTicketId: number) =>
    request<{ ok: boolean }>(`/ai/tickets/${ticketId}/link-incident`, { method: 'POST', body: JSON.stringify({ parent_ticket_id: parentTicketId }) }),
  unlinkIncident: (ticketId: number) =>
    request<{ ok: boolean }>(`/ai/tickets/${ticketId}/unlink-incident`, { method: 'POST' }),
  getIncidentChildren: (ticketId: number) =>
    request<Array<{ id: number; ticket_number: string; subject: string; status: string; priority: string }>>(`/ai/tickets/${ticketId}/incident-children`),

  // Tenant plan management
  getTenantPlan: (tenantId: number) => request<any>(`/admin/tenants/${tenantId}/plan`),
  updateTenantPlan: (tenantId: number, data: { plan_tier?: string; extend_days?: number; plan_expires_at?: string | null }) =>
    request<{ ok: boolean }>(`/admin/tenants/${tenantId}/plan`, { method: 'PUT', body: JSON.stringify(data) }),

  // RBAC Groups & Permissions
  listGroups: (tenantId?: number) => {
    const qs = tenantId ? `?tenant_id=${tenantId}` : '';
    return request<any[]>(`/admin/groups${qs}`);
  },
  createGroup: (data: { name: string; tenant_id?: number; description?: string }) =>
    request<{ id: number }>('/admin/groups', { method: 'POST', body: JSON.stringify(data) }),
  deleteGroup: (id: number) =>
    request<{ ok: boolean }>(`/admin/groups/${id}`, { method: 'DELETE' }),
  getGroupMembers: (groupId: number) =>
    request<any[]>(`/admin/groups/${groupId}/members`),
  setGroupMembers: (groupId: number, userIds: number[]) =>
    request<{ ok: boolean }>(`/admin/groups/${groupId}/members`, { method: 'PUT', body: JSON.stringify({ user_ids: userIds }) }),
  getUserLocations: (userId: number) =>
    request<{ location_id: number; name: string; parent_id: number | null }[]>(`/admin/users/${userId}/locations`),
  setUserLocations: (userId: number, locationIds: number[]) =>
    request<{ ok: boolean }>(`/admin/users/${userId}/locations`, { method: 'PUT', body: JSON.stringify({ location_ids: locationIds }) }),

  getUserGroups: (userId: number) =>
    request<{ id: number; name: string }[]>(`/admin/users/${userId}/groups`),
  setUserGroups: (userId: number, groupIds: number[]) =>
    request<{ ok: boolean }>(`/admin/users/${userId}/groups`, { method: 'PUT', body: JSON.stringify({ group_ids: groupIds }) }),
  getUserTeams: (userId: number) =>
    request<{ id: number; name: string; role: string }[]>(`/admin/users/${userId}/teams`),
  setUserTeams: (userId: number, teamIds: number[]) =>
    request<{ ok: boolean }>(`/admin/users/${userId}/teams`, { method: 'PUT', body: JSON.stringify({ team_ids: teamIds }) }),
  getUserPermissions: (userId: number) =>
    request<{ user_id: number; role: string; effective_permissions: string[]; groups: any[]; overrides: { slug: string; label: string; granted: boolean; reason: string }[] }>(`/admin/users/${userId}/permissions`),
  setUserPermissionOverrides: (userId: number, overrides: { permission_id: number; granted: boolean; reason?: string }[]) =>
    request<{ ok: boolean }>(`/admin/users/${userId}/permissions/overrides`, { method: 'PUT', body: JSON.stringify({ overrides }) }),
  getPermissionMatrix: (tenantId?: number) => {
    const qs = tenantId ? `?tenant_id=${tenantId}` : '';
    return request<{ groups: any[]; permissions: any[]; matrix: Record<string, number[]> }>(`/admin/permissions/matrix${qs}`);
  },
  savePermissionMatrix: (groups: Record<number, number[]>) =>
    request<{ ok: boolean; updated: number }>('/admin/permissions/matrix', { method: 'PUT', body: JSON.stringify({ groups }) }),

  // Notification groups
  listNotificationGroups: () => request<any[]>('/notifications/groups'),
  createNotificationGroup: (data: { name: string; description?: string }) =>
    request<{ id: number }>('/notifications/groups', { method: 'POST', body: JSON.stringify(data) }),
  updateNotificationGroup: (id: number, data: any) =>
    request<{ ok: boolean }>(`/notifications/groups/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteNotificationGroup: (id: number) =>
    request<{ ok: boolean }>(`/notifications/groups/${id}`, { method: 'DELETE' }),
  listGroupMembers: (groupId: number) => request<any[]>(`/notifications/groups/${groupId}/members`),
  addGroupMember: (groupId: number, data: { user_id?: number; email?: string }) =>
    request<{ ok: boolean }>(`/notifications/groups/${groupId}/members`, { method: 'POST', body: JSON.stringify(data) }),
  removeGroupMember: (groupId: number, memberId: number) =>
    request<{ ok: boolean }>(`/notifications/groups/${groupId}/members/${memberId}`, { method: 'DELETE' }),
  getNotificationSettings: () => request<any>('/notifications/settings'),
  updateNotificationSettings: (data: any) =>
    request<{ ok: boolean }>('/notifications/settings', { method: 'PUT', body: JSON.stringify(data) }),

  // Notification preferences
  getNotificationPreferences: () => request<any[]>('/notifications/preferences'),
  updateNotificationPreferences: (prefs: any[]) =>
    request<{ ok: boolean }>('/notifications/preferences', { method: 'PUT', body: JSON.stringify({ preferences: prefs }) }),

  // Notification group × event matrix
  getGroupEventMatrix: () => request<import('../types').GroupEventMatrixEntry[]>('/notifications/group-event-matrix'),
  updateGroupEvents: (groupId: number, events: import('../types').GroupEventSubscription[]) =>
    request<{ ok: boolean }>(`/notifications/groups/${groupId}/events`, {
      method: 'PUT',
      body: JSON.stringify({ events }),
    }),

  getTeamEventMatrix: () => request<import('../types').TeamEventMatrixEntry[]>('/notifications/team-event-matrix'),
  updateTeamEvents: (teamId: number, events: import('../types').GroupEventSubscription[]) =>
    request<{ ok: boolean }>(`/notifications/teams/${teamId}/events`, {
      method: 'PUT',
      body: JSON.stringify({ events }),
    }),

  // Notification email templates
  getNotificationTemplates: () => request<import('../types').NotificationTemplate[]>('/notifications/templates'),
  updateNotificationTemplate: (event: string, data: { subject_template: string; body_headline: string; body_intro: string }) =>
    request<{ ok: boolean }>(`/notifications/templates/${event}`, { method: 'PUT', body: JSON.stringify(data) }),
  resetNotificationTemplate: (event: string) =>
    request<{ ok: boolean }>(`/notifications/templates/${event}`, { method: 'DELETE' }),

  // In-app notifications (bell)
  getUnreadNotifications: () =>
    request<{ count: number; notifications: Array<{ id: number; ticket_id: number; event: string; ticket_number: string; subject: string; status: string; priority: string; created_at: string }> }>('/notifications/in-app/unread'),
  markNotificationsRead: (ids?: number[]) =>
    request<{ ok: boolean }>('/notifications/in-app/read', { method: 'POST', body: JSON.stringify({ ids: ids || [] }) }),

  // Pipeline Queue
  getQueueStats: () => request<import('../types').QueueStats>('/queue/stats'),
  getQueueActive: () => request<{ tasks: import('../types').QueueTask[] }>('/queue/active'),
  getQueueRecent: (params?: Record<string, string>) => {
    const qs = params ? '?' + new URLSearchParams(params).toString() : '';
    return request<{ executions: import('../types').PipelineExecution[]; total: number }>(`/queue/recent${qs}`);
  },
  getQueueFailures: () => request<{ tasks: import('../types').QueueTask[] }>('/queue/failures'),
  retryQueueTask: (id: number) =>
    request<{ ok: boolean }>(`/queue/${id}/retry`, { method: 'POST' }),
  cancelQueueTask: (id: number) =>
    request<{ ok: boolean }>(`/queue/${id}/cancel`, { method: 'POST' }),
  getQueueSchedules: () => request<{ schedules: import('../types').PipelineSchedule[] }>('/queue/schedules'),
  toggleQueueSchedule: (id: number, enabled: boolean) =>
    request<{ ok: boolean }>(`/queue/schedules/${id}/toggle`, { method: 'POST', body: JSON.stringify({ enabled }) }),

  // Reports
  getReportConfig: () =>
    request<{ plan_tier: string; reports: Array<{ id: string; tier: string; accessible: boolean }> }>('/reports/config'),

  getTicketVolumeReport: (params: Record<string, string>) => {
    const qs = '?' + new URLSearchParams(params).toString();
    return request<{ rows: any[] }>(`/reports/ticket-volume${qs}`);
  },

  getStatusBreakdown: (params?: Record<string, string>) => {
    const qs = params && Object.keys(params).length ? '?' + new URLSearchParams(params).toString() : '';
    return request<{ rows: any[] }>(`/reports/status-breakdown${qs}`);
  },

  getCategoryBreakdown: (params: Record<string, string>) => {
    const qs = '?' + new URLSearchParams(params).toString();
    return request<{ rows: any[] }>(`/reports/category-breakdown${qs}`);
  },

  getSlaCompliance: (params: Record<string, string>) => {
    const qs = '?' + new URLSearchParams(params).toString();
    return request<{ performance: any[]; policies: any[] }>(`/reports/sla-compliance${qs}`);
  },

  getAgentPerformance: (params: Record<string, string>) => {
    const qs = '?' + new URLSearchParams(params).toString();
    return request<{ agents: any[] }>(`/reports/agent-performance${qs}`);
  },

  getAiEffectiveness: (params: Record<string, string>) => {
    const qs = '?' + new URLSearchParams(params).toString();
    return request<{ summary: any; cost: any }>(`/reports/ai-effectiveness${qs}`);
  },

  getTicketVolumeBreakdown: (params: Record<string, string>) => {
    const qs = '?' + new URLSearchParams(params).toString();
    return request<{ agents: any[] }>(`/reports/ticket-volume/breakdown${qs}`);
  },

  getRoutingInsights: (params: Record<string, string>) => {
    const qs = Object.keys(params).length ? '?' + new URLSearchParams(params).toString() : '';
    return request<{ category_coverage: any[]; agent_specializations: any[]; coverage_gaps: any[] }>(`/reports/routing-insights${qs}`);
  },

  getAgingTickets: (params: Record<string, string>) => {
    const qs = Object.keys(params).length ? '?' + new URLSearchParams(params).toString() : '';
    return request<{ buckets: any[]; stale_tickets: any[] }>(`/reports/aging-tickets${qs}`);
  },

  getLocationBreakdown: (params: Record<string, string>) => {
    const qs = '?' + new URLSearchParams(params).toString();
    return request<{ rows: any[] }>(`/reports/location-breakdown${qs}`);
  },

  exportTicketsCsv: async (params: Record<string, string>) => {
    const qs = Object.keys(params).length ? '?' + new URLSearchParams(params).toString() : '';
    const res = await fetch(`${BASE}/reports/ticket-export/csv${qs}`, { credentials: 'include' });
    if (!res.ok) {
      const body = await res.json().catch(() => ({ error: res.statusText }));
      throw new Error(body.error || `HTTP ${res.status}`);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `tickets-export.csv`;
    a.click();
    URL.revokeObjectURL(url);
  },

  // Automations
  listAutomations: () => request<import('../types').Automation[]>('/automations'),
  createAutomation: (data: { name: string; trigger_type: string; description?: string; trigger_config?: Record<string, any> }) =>
    request<{ id: number }>('/automations', { method: 'POST', body: JSON.stringify(data) }),
  getAutomation: (id: number) => request<import('../types').Automation>(`/automations/${id}`),
  updateAutomation: (id: number, data: Partial<import('../types').Automation>) =>
    request<{ ok: boolean }>(`/automations/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteAutomation: (id: number) =>
    request<{ ok: boolean }>(`/automations/${id}`, { method: 'DELETE' }),
  saveAutomationCanvas: (id: number, data: { nodes: any[]; edges: any[] }) =>
    request<{ ok: boolean; nodes: number; edges: number }>(`/automations/${id}/canvas`, { method: 'PUT', body: JSON.stringify(data) }),
  toggleAutomation: (id: number) =>
    request<{ is_active: boolean }>(`/automations/${id}/toggle`, { method: 'POST' }),
  listAutomationRuns: (id: number, limit?: number) => {
    const qs = limit ? `?limit=${limit}` : '';
    return request<import('../types').AutomationRun[]>(`/automations/${id}/runs${qs}`);
  },
  exportReportCsv: async (reportId: string, params: Record<string, string>) => {
    const qs = Object.keys(params).length ? '?' + new URLSearchParams(params).toString() : '';
    const res = await fetch(`${BASE}/reports/${reportId}/csv${qs}`, { credentials: 'include' });
    if (!res.ok) {
      const body = await res.json().catch(() => ({ error: res.statusText }));
      throw new Error(body.error || `HTTP ${res.status}`);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${reportId}-report.csv`;
    a.click();
    URL.revokeObjectURL(url);
  },

  // Billing
  getBillingUsage: () => request<any>('/billing/usage'),
  createCheckoutSession: (tier: string) =>
    request<{ url: string }>('/billing/checkout', { method: 'POST', body: JSON.stringify({ tier }) }),
  openBillingPortal: () =>
    request<{ url: string }>('/billing/portal', { method: 'POST' }),
  getBYOKKeys: () => request<{
    anthropic: string | null;
    openai: string | null;
    voyage: string | null;
    resend: string | null;
    twilio_account_sid: string | null;
    twilio_auth_token: string | null;
    twilio_phone_number: string | null;
    elevenlabs: string | null;
  }>('/billing/byok'),
  setBYOKKeys: (keys: Record<string, string>) =>
    request<{ status: string; validated: Record<string, boolean> }>('/billing/byok', {
      method: 'PUT',
      body: JSON.stringify(keys),
    }),

  // Phone Helpdesk
  getPhoneConfig:    () => request<any>('/phone/config'),
  savePhoneConfig:   (data: any) => request<any>('/phone/config', { method: 'PUT', body: JSON.stringify(data) }),
  enablePhone:       () => request<any>('/phone/enable', { method: 'POST', body: '{}' }),
  listPhoneSessions: (limit = 50, agentId?: number) => {
    const q = agentId ? `?limit=${limit}&agent_id=${agentId}` : `?limit=${limit}`;
    return request<any[]>(`/phone/sessions${q}`);
  },
  getPhoneDefaults:  () => request<Record<string, any>>('/phone/config/defaults'),
  getPhoneWebhooks:  () => request<Record<string, string>>('/phone/webhooks'),

  // Phone Agents (multi-agent)
  listPhoneAgents:     () => request<any[]>('/phone/agents'),
  getPhoneAgent:       (id: number) => request<any>(`/phone/agents/${id}`),
  createPhoneAgent:    (data: any) => request<any>('/phone/agents', { method: 'POST', body: JSON.stringify(data) }),
  updatePhoneAgent:    (id: number, data: any) => request<any>(`/phone/agents/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deletePhoneAgent:    (id: number) => request<any>(`/phone/agents/${id}`, { method: 'DELETE' }),
  deployPhoneAgent:    (id: number) => request<any>(`/phone/agents/${id}/deploy`, { method: 'POST', body: '{}' }),
  activatePhoneAgent:  (id: number) => request<any>(`/phone/agents/${id}/activate`, { method: 'POST', body: '{}' }),
  resetPhoneAgent:     (id: number) => request<any>(`/phone/agents/${id}/reset`, { method: 'POST', body: '{}' }),
  getDefaultPrompt:    (language = 'en', agentName?: string) => {
    const params = new URLSearchParams({ language });
    if (agentName) params.set('agent_name', agentName);
    return request<{ prompt: string; language: string }>(`/phone/agents/default-prompt?${params}`);
  },

  // Messaging (SMS + WhatsApp)
  getMessagingConfig:    () => request<any>('/messaging/config'),
  saveMessagingConfig:   (data: any) => request<any>('/messaging/config', { method: 'PUT', body: JSON.stringify(data) }),
  getMessagingWebhooks:  () => request<Record<string, string>>('/messaging/webhooks'),
  getMessagingStats:     () => request<any>('/messaging/stats'),

  listMsgConversations: (channel?: string, status?: string, limit = 50, offset = 0) => {
    const q = new URLSearchParams();
    if (channel) q.set('channel', channel);
    if (status) q.set('status', status);
    q.set('limit', String(limit));
    q.set('offset', String(offset));
    return request<any[]>(`/messaging/conversations?${q}`);
  },
  getMsgConversation:    (id: number) => request<any>(`/messaging/conversations/${id}`),
  updateMsgConversation: (id: number, data: any) => request<any>(`/messaging/conversations/${id}`, { method: 'PUT', body: JSON.stringify(data) }),

  getMsgMessages:  (convId: number, limit = 100) => request<any[]>(`/messaging/conversations/${convId}/messages?limit=${limit}`),
  sendMsgMessage:  (convId: number, body: string, templateName?: string) =>
    request<any>(`/messaging/conversations/${convId}/messages`, {
      method: 'POST', body: JSON.stringify({ body, template_name: templateName }),
    }),

  listMessagingTemplates:  () => request<any[]>('/messaging/templates'),
  createMessagingTemplate: (data: any) => request<any>('/messaging/templates', { method: 'POST', body: JSON.stringify(data) }),
  updateMessagingTemplate: (id: number, data: any) => request<any>(`/messaging/templates/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteMessagingTemplate: (id: number) => request<any>(`/messaging/templates/${id}`, { method: 'DELETE' }),

  // Status Page
  listStatusIncidents: () => request<any[]>('/status/incidents'),
  getStatusIncident: (id: number) => request<any>(`/status/incidents/${id}`),
  createStatusIncident: (data: any) => request<any>('/status/incidents', { method: 'POST', body: JSON.stringify(data) }),
  updateStatusIncident: (id: number, data: any) => request<any>(`/status/incidents/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteStatusIncident: (id: number) => request<any>(`/status/incidents/${id}`, { method: 'DELETE' }),
  addStatusUpdate: (id: number, data: any) => request<any>(`/status/incidents/${id}/updates`, { method: 'POST', body: JSON.stringify(data) }),

  // Setup status (activation checklist)
  getSetupStatus: () => request<{
    complete: boolean;
    steps: {
      ai_enabled: boolean;
      categories_configured: boolean;
      team_invited: boolean;
      kb_created: boolean;
      first_ticket: boolean;
    };
  }>('/admin/setup-status'),

  enableAI: () => request<{ ok: boolean }>('/admin/setup/enable-ai', { method: 'POST' }),

  // System Errors (Platform Admin — super_admin only)
  listSystemErrors: (params?: Record<string, string>) => {
    const qs = params ? '?' + new URLSearchParams(params).toString() : '';
    return request<{ errors: any[]; total: number }>(`/admin/system-errors${qs}`);
  },
  resolveSystemError: (id: number, notes?: string | null) =>
    request<{ ok: boolean }>(`/admin/system-errors/${id}/resolve`, {
      method: 'PUT',
      body: JSON.stringify({ notes: notes ?? null }),
    }),
  deleteSystemError: (id: number) =>
    request<{ ok: boolean }>(`/admin/system-errors/${id}`, { method: 'DELETE' }),

  // User profile (SMS opt-in / phone number)
  getProfile: () =>
    request<import('../types').UserProfile>('/auth/profile'),
  updateProfile: (data: { phone_number?: string | null; sms_opted_in?: boolean }) =>
    request<import('../types').UserProfile>('/auth/profile', { method: 'PUT', body: JSON.stringify(data) }),
};
