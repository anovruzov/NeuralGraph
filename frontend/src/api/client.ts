// Typed fetch wrapper for the Mycelic API (docs/mycelic/API.md). One function per endpoint.
import type * as T from './types';

export const API_BASE = '/api';

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly body: T.ApiErrorBody | null;
  readonly requestId: string | null;

  constructor(status: number, code: string, message: string, body: T.ApiErrorBody | null, requestId: string | null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.body = body;
    this.requestId = requestId;
  }
}

type UnauthorizedHandler = () => void;
let onUnauthorized: UnauthorizedHandler | null = null;

/** Registered by the AuthProvider so a 401 anywhere sends the user to /login. */
export function setUnauthorizedHandler(fn: UnauthorizedHandler | null): void {
  onUnauthorized = fn;
}

export type Query = Record<string, string | number | boolean | null | undefined>;

function qs(query?: Query): string {
  if (!query) return '';
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === '' || v === false) continue;
    p.set(k, v === true ? '1' : String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : '';
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
  form?: FormData;
  query?: Query;
  signal?: AbortSignal;
  /** Skip the global 401 handler (used by /auth/me on first load). */
  silent401?: boolean;
}

export async function request<R>(path: string, opts: RequestOptions = {}): Promise<R> {
  const headers: Record<string, string> = { Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest' };
  let body: BodyInit | undefined;
  if (opts.form) {
    body = opts.form;
  } else if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(opts.body);
  }
  const res = await fetch(`${API_BASE}${path}${qs(opts.query)}`, {
    method: opts.method ?? (opts.body !== undefined || opts.form ? 'POST' : 'GET'),
    credentials: 'include',
    headers,
    body,
    signal: opts.signal ?? null,
  });
  const requestId = res.headers.get('X-Request-Id');
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }
  if (!res.ok) {
    const errBody = (data && typeof data === 'object' ? (data as T.ApiErrorBody) : null);
    const message = errBody?.error || (text && text.length < 300 ? text : '') || `${res.status} ${res.statusText}`;
    const code = errBody?.code || (res.status === 401 ? 'unauthorized' : res.status === 403 ? 'forbidden' : 'error');
    if (res.status === 401 && !opts.silent401 && onUnauthorized) onUnauthorized();
    throw new ApiError(res.status, code, message, errBody, requestId);
  }
  return data as R;
}

const get = <R>(path: string, query?: Query, silent401 = false) => request<R>(path, { method: 'GET', query, silent401 });
const post = <R>(path: string, body?: unknown, query?: Query) => request<R>(path, { method: 'POST', body: body ?? {}, query });
const put = <R>(path: string, body: unknown) => request<R>(path, { method: 'PUT', body });
const patch = <R>(path: string, body: unknown) => request<R>(path, { method: 'PATCH', body });
const del = <R>(path: string, body?: unknown) => request<R>(path, { method: 'DELETE', body });

// ---------------------------------------------------------------- auth and onboarding
export const auth = {
  register: (body: T.RegisterBody) => post<T.AuthResponse>('/auth/register', body),
  login: (body: { email: string; password: string; tenant_slug?: string }) => post<T.AuthResponse>('/auth/login', body),
  logout: () => post<{ ok: boolean }>('/auth/logout'),
  me: () => get<T.MeResponse>('/auth/me', undefined, true),
  invitation: (token: string) => get<{ invitation: T.Invitation }>(`/auth/invitation/${encodeURIComponent(token)}`, undefined, true),
  acceptInvitation: (token: string, body: { name: string; password: string }) =>
    post<T.AuthResponse>(`/auth/invitation/${encodeURIComponent(token)}/accept`, body),
  demoPersonas: () => get<T.ListResponse<T.DemoPersona>>('/demo/personas', undefined, true),
  demoSwitch: (user_id: string) => post<T.AuthResponse>('/demo/switch', { user_id }),
};

// ---------------------------------------------------------------- organization
export const org = {
  get: () => get<T.OrgResponse>('/org'),
  createUnit: (body: { type: string; name: string; parent_id?: string | null; settings?: T.Json }) => post<{ unit: T.Unit }>('/org/units', body),
  updateUnit: (unit_id: string, body: { name?: string; parent_id?: string | null; archived?: boolean }) =>
    patch<{ unit: T.Unit }>(`/org/units/${unit_id}`, body),
  setProjectScope: (unit_id: string, unit_ids: string[]) => put<{ unit_ids: string[] }>(`/org/units/${unit_id}/scope`, { unit_ids }),
  unit: (unit_id: string) => get<T.UnitDetailResponse>(`/org/units/${unit_id}`),
  users: () => get<T.ListResponse<T.User>>('/org/users'),
  updateUser: (user_id: string, body: { status?: 'active' | 'disabled'; name?: string }) => patch<{ user: T.User }>(`/org/users/${user_id}`, body),
  memberships: () => get<T.ListResponse<T.MembershipRow>>('/org/memberships'),
  addMembership: (body: { user_id: string; unit_id: string; role: string }) => post<{ membership: T.MembershipRow }>('/org/memberships', body),
  revokeMembership: (body: { user_id: string; unit_id: string; role?: string }) => del<{ revoked: number }>('/org/memberships', body),
  invitations: () => get<T.ListResponse<T.Invitation>>('/org/invitations'),
  invite: (body: { email: string; role: string; unit_id?: string }) => post<{ invitation: T.Invitation; accept_url: string }>('/org/invitations', body),
  deleteInvitation: (invitation_id: string) => del<{ ok: boolean }>(`/org/invitations/${invitation_id}`),
  policies: () => get<{ policies: T.Policies }>('/org/policies'),
  setPolicy: (key: string, value: unknown) => put<{ policies: T.Policies }>('/org/policies', { key, value }),
  network: (query: { view: T.NetworkView; goal_id?: string; unit_id?: string }) => get<T.NetworkResponse>('/org/network', query),
};

// ---------------------------------------------------------------- grants and holders
export const grants = {
  list: () => get<{ given: T.Grant[]; received: T.Grant[] }>('/grants'),
  create: (body: T.GrantBody) => post<{ grant: T.Grant }>('/grants', body),
  revoke: (grant_id: string) => del<{ ok: boolean }>(`/grants/${grant_id}`),
};

export const holders = {
  list: () => get<T.ListResponse<T.Holder>>('/holders'),
  create: (body: T.HolderCreateBody) => post<{ holder: T.Holder; key: string }>('/holders', body),
  update: (holder_id: string, body: { export_policy?: T.ExportPolicy; domains?: string[]; name?: string; status?: 'revoked' }) =>
    patch<{ holder: T.Holder }>(`/holders/${holder_id}`, body),
  rotateKey: (holder_id: string) => post<{ key: string }>(`/holders/${holder_id}/rotate-key`),
  addDocument: (holder_id: string, body: T.DocumentBody) => post<{ document: T.Document }>(`/holders/${holder_id}/documents`, body),
  uploadDocument: (holder_id: string, file: File, extra: { title?: string; kind?: string; domains?: string[] } = {}) => {
    const form = new FormData();
    form.append('file', file, file.name);
    if (extra.title) form.append('title', extra.title);
    if (extra.kind) form.append('kind', extra.kind);
    if (extra.domains?.length) form.append('domains', extra.domains.join(','));
    return request<{ document: T.Document }>(`/holders/${holder_id}/documents`, { method: 'POST', form });
  },
  documents: (holder_id: string) => get<T.ListResponse<T.Document>>(`/holders/${holder_id}/documents`),
  document: (holder_id: string, doc_id: string) => get<T.DocumentDetail>(`/holders/${holder_id}/documents/${doc_id}`),
  revise: (holder_id: string, doc_id: string, body: { text: string; title?: string; observed_at?: string }) =>
    post<{ document: T.Document }>(`/holders/${holder_id}/documents/${doc_id}/revise`, body),
  retract: (holder_id: string, doc_id: string, reason: string) =>
    post<{ document: T.Document }>(`/holders/${holder_id}/documents/${doc_id}/retract`, { reason }),
  search: (holder_id: string, q: string, k = 10) => get<T.SearchResponse>(`/holders/${holder_id}/search`, { q, k }),
  stats: (holder_id: string) => get<T.HolderStats>(`/holders/${holder_id}/stats`),
};

export const memory = {
  search: (q: string, k = 10) => get<T.SearchResponse>('/me/memory/search', { q, k }),
  recent: (limit = 20) => get<T.ListResponse<T.MemoryResult>>('/me/memory/recent', { limit }),
};

// ---------------------------------------------------------------- chat
export const chats = {
  list: () => get<T.ListResponse<T.Chat>>('/chats'),
  create: (body: { agent_type: 'user' | 'unit'; agent_id: string }) => post<{ chat: T.Chat }>('/chats', body),
  get: (chat_id: string) => get<T.ChatDetail>(`/chats/${chat_id}`),
  send: (chat_id: string, text: string) => post<T.SendMessageResponse>(`/chats/${chat_id}/messages`, { text }),
};

// ---------------------------------------------------------------- goals and loops
export const goals = {
  list: (query: { scope_unit_id?: string; status?: string; mine?: boolean; parent_goal_id?: string; include_archived?: boolean; limit?: number } = {}) =>
    get<T.ListResponse<T.Goal>>('/goals', query),
  create: (body: T.GoalCreateBody) => post<{ goal: T.Goal }>('/goals', body),
  get: (goal_id: string) => get<T.GoalDetail>(`/goals/${goal_id}`),
  update: (goal_id: string, body: Partial<T.GoalCreateBody>) => patch<{ goal: T.Goal }>(`/goals/${goal_id}`, body),
  action: (goal_id: string, body: T.GoalActionBody) => post<{ goal: T.Goal; subgoals?: T.Goal[] }>(`/goals/${goal_id}/actions`, body),
  recordOutcome: (goal_id: string, body: T.OutcomeBody) => post<{ outcome: T.Outcome; progress: T.Progress }>(`/goals/${goal_id}/outcomes`, body),
  loop: (goal_id: string) => get<{ loop: T.Loop }>(`/goals/${goal_id}/loop`),
  loopAction: (goal_id: string, action: T.LoopAction) => post<{ loop: T.Loop }>(`/goals/${goal_id}/loop`, { action }),
};

// ---------------------------------------------------------------- questions
export const questions = {
  list: (query: { goal_id?: string; status?: string; needs_input?: boolean; scope_unit_id?: string; limit?: number } = {}) =>
    get<T.ListResponse<T.Question>>('/questions', query),
  create: (body: T.QuestionCreateBody) => post<{ question: T.Question }>('/questions', body),
  get: (question_id: string) => get<T.QuestionDetail>(`/questions/${question_id}`),
  respond: (question_id: string, body: T.RespondBody) => post<{ response: T.Response }>(`/questions/${question_id}/respond`, body),
  cancel: (question_id: string) => post<{ question: T.Question }>(`/questions/${question_id}/cancel`),
};

// ---------------------------------------------------------------- discoveries, claims, evidence, conflicts
export const discoveries = {
  list: (query: { level?: string; scope_unit_id?: string; status?: string; goal_id?: string; kind?: string; limit?: number } = {}) =>
    get<T.ListResponse<T.Discovery>>('/discoveries', query),
  get: (id: string) => get<T.DiscoveryDetail>(`/discoveries/${id}`),
  review: (id: string, body: { action: T.ReviewAction; note?: string }) => post<{ discovery: T.Discovery }>(`/discoveries/${id}/review`, body),
};

export const claims = {
  list: (query: { scope_unit_id?: string; status?: string; goal_id?: string; q?: string; limit?: number } = {}) =>
    get<T.ListResponse<T.Claim>>('/claims', query),
  get: (id: string) => get<T.ClaimDetail>(`/claims/${id}`),
  retract: (id: string, reason: string) => post<{ claim: T.Claim }>(`/claims/${id}/retract`, { reason }),
};

export const evidence = {
  get: (ref_id: string) => get<T.EvidenceDetail>(`/evidence/${ref_id}`),
  raw: (ref_id: string) => get<T.EvidenceRawResponse>(`/evidence/${ref_id}/raw`),
};

export const conflicts = {
  list: (query: { status?: string; scope_unit_id?: string; limit?: number } = {}) => get<T.ListResponse<T.Conflict>>('/conflicts', query),
  resolve: (id: string, body: { outcome: T.ConflictOutcome; note: string }) => post<{ conflict: T.Conflict }>(`/conflicts/${id}/resolve`, body),
  investigate: (id: string) => post<{ conflict: T.Conflict; question: T.Question }>(`/conflicts/${id}/investigate`),
};

// ---------------------------------------------------------------- workspaces and admin
export const workspace = {
  employee: () => get<T.EmployeeWorkspace>('/workspace/employee'),
  unit: (unit_id: string) => get<T.UnitWorkspace>(`/workspace/unit/${unit_id}`),
  executive: () => get<T.ExecutiveWorkspace>('/workspace/executive'),
};

export const admin = {
  overview: () => get<T.AdminOverview>('/admin/overview'),
  jobs: (query: { status?: string; kind?: string; limit?: number } = {}) => get<T.ListResponse<T.Job>>('/admin/jobs', query),
  retryJob: (id: string) => post<{ job: T.Job }>(`/admin/jobs/${id}/retry`),
  retryDead: () => post<{ retried: number }>('/admin/jobs/retry-dead'),
  audit: (query: { limit?: number; action?: string; resource_id?: string } = {}) => get<T.ListResponse<T.AuditRow>>('/admin/audit', query),
  usage: (since?: string) => get<{ items: T.UsageRow[]; totals: T.Json }>('/admin/usage', { since }),
  models: () => get<T.ModelsResponse>('/admin/models'),
  setModels: (policy_tiers: Record<string, string>) => put<T.ModelsResponse>('/admin/models', { policy_tiers }),
  workers: () => get<T.ListResponse<T.Worker> | { workers: T.Worker[] }>('/admin/workers'),
};

export const notifications = {
  list: (unread = false, limit = 50) => get<T.ListResponse<T.Notification>>('/notifications', { unread, limit }),
  markRead: (ids?: number[]) => post<{ updated: number }>('/notifications/read', ids ? { ids } : {}),
};

export const health = {
  healthz: () => fetch('/healthz', { credentials: 'include' }).then((r) => r.json() as Promise<{ ok: boolean; service: string; version: string }>),
  readyz: () =>
    fetch('/readyz', { credentials: 'include' }).then(
      (r) => r.json() as Promise<{ ok: boolean; db: boolean; transport: string | boolean; worker_heartbeat_age_seconds: number | null; migrations_pending: number | boolean }>,
    ),
};

export const api = { auth, org, grants, holders, memory, chats, goals, questions, discoveries, claims, evidence, conflicts, workspace, admin, notifications, health };
export default api;
