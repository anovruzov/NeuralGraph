// TypeScript types for every JSON shape in docs/mycelic/API.md.
// Field names follow the contract exactly; optional/nullable fields are marked where the API may omit them.

export type Json = Record<string, unknown>;

// ---------------------------------------------------------------- organization
export type UnitType = 'executive' | 'region' | 'subsidiary' | 'department' | 'team' | 'project';
export type Level = 'employee' | 'team' | 'department' | 'subsidiary' | 'region' | 'executive';
export type Role = 'employee' | 'team_lead' | 'department_lead' | 'subsidiary_lead' | 'regional_lead' | 'executive' | 'org_admin';

export const UNIT_TYPES: UnitType[] = ['executive', 'region', 'subsidiary', 'department', 'team', 'project'];
export const ROLES: Role[] = ['employee', 'team_lead', 'department_lead', 'subsidiary_lead', 'regional_lead', 'executive', 'org_admin'];
export const LEVEL_ORDER: Level[] = ['employee', 'team', 'department', 'subsidiary', 'region', 'executive'];

export interface Membership {
  unit_id: string;
  role: Role | string;
  unit_type: UnitType | string | null;
  unit_name: string | null;
  level: Level | string | null;
}

export interface Principal {
  tenant_id: string;
  kind: 'user' | 'holder' | 'worker' | 'system' | 'loop' | string;
  id: string;
  name: string;
  is_admin: boolean;
  is_demo: boolean;
  session_kind?: string;
  highest_level: Level | string;
  roles: string[];
  holder_ids: string[];
  memberships: Membership[];
}

export interface Tenant {
  tenant_id: string;
  name: string;
  slug: string;
  is_demo: boolean;
  settings?: Json;
  created_at?: string;
}

export interface User {
  user_id: string;
  email: string;
  name: string;
  status: 'active' | 'disabled' | string;
  is_demo: boolean | number;
  created_at: string;
  updated_at?: string;
  memberships?: MembershipRow[];
}

export interface OnboardingSteps {
  organization: boolean;
  members: boolean;
  holder: boolean;
  model: boolean;
  goal: boolean;
  loop_active: boolean;
}

export interface Onboarding {
  complete: boolean;
  steps: OnboardingSteps;
  demo_goal_id?: string | null;
}

export interface MeResponse {
  user: User;
  tenant: Tenant;
  principal: Principal;
  levels: string[];
  holders: Holder[];
  unread_notifications: number;
  demo_mode: boolean;
  onboarding: Onboarding;
}

export interface AuthResponse {
  user: User;
  principal: Principal;
  tenant?: Tenant;
}

export interface RegisterBody {
  org_name: string;
  slug: string;
  email: string;
  name: string;
  password: string;
}

export interface Invitation {
  invitation_id?: string;
  email: string;
  role: string;
  unit_id?: string | null;
  unit_name?: string | null;
  org_name?: string;
  status: string;
  created_at?: string;
  expires_at?: string | null;
}

export interface DemoPersona {
  user_id: string;
  name: string;
  email: string;
  roles: string[];
  unit_names: string[];
  level: string;
}

export interface Unit {
  unit_id: string;
  type: UnitType | string;
  level: Level | string;
  name: string;
  parent_id: string | null;
  path: string;
  depth: number;
  settings: Json;
  archived_at: string | null;
  member_count: number;
}

export interface UnitTreeNode {
  unit: Unit;
  children: UnitTreeNode[];
}

export interface OrgResponse {
  tenant: Tenant;
  units: Unit[];
  tree: UnitTreeNode[];
  counts: { users: number; memberships: number; holders: number };
}

export interface UnitMember {
  user_id: string;
  user_name: string;
  email: string;
  role: string;
}

export interface UnitDetailResponse {
  unit: Unit;
  members: UnitMember[];
  children: Unit[];
  projects: Unit[];
  holders: Holder[];
}

export interface MembershipRow {
  membership_id: string;
  user_id: string;
  user_name: string;
  email: string;
  unit_id: string;
  unit_name: string;
  unit_type: string;
  role: string;
  status: string;
  created_at: string;
}

export interface Policies {
  min_independent_roots: number;
  freshness_days: number;
  default_goal_budget: Budget;
  priority_weights: PriorityWeights;
  model_tiers: Record<string, string>;
  holder_default_export: ExportPolicy;
  [key: string]: unknown;
}

export interface PriorityWeights {
  goal_value: number;
  uncertainty: number;
  impact: number;
  missing_evidence: number;
  information_gain: number;
  cost: number;
  [key: string]: number;
}

export interface ExportPolicy {
  disclosure?: 'excerpt' | 'summary' | 'none' | string;
  max_excerpt_chars?: number;
  answer_scopes?: string[];
  [key: string]: unknown;
}

export type NetworkView = 'hierarchy' | 'collaboration' | 'lineage';
export type NetworkNodeType = 'unit' | 'user' | 'holder' | 'goal' | 'question' | 'claim' | 'discovery' | 'evidence';
export type NetworkEdgeKind =
  | 'parent' | 'member' | 'leads' | 'owns' | 'routed' | 'responded' | 'supports' | 'derived' | 'conflicts' | 'scoped' | 'project';

export interface NetworkNode {
  id: string;
  type: NetworkNodeType | string;
  label: string;
  level?: string | null;
  unit_id?: string | null;
  status?: string | null;
  meta?: Json;
}

export interface NetworkEdge {
  source: string;
  target: string;
  kind: NetworkEdgeKind | string;
}

export interface NetworkResponse {
  nodes: NetworkNode[];
  edges: NetworkEdge[];
}

// ---------------------------------------------------------------- grants and holders
export type GrantLevel = 'read' | 'artifact' | 'raw';
export type GrantResourceType = 'holder' | 'claim' | 'discovery' | 'goal' | 'evidence_ref';

export interface Grant {
  grant_id: string;
  tenant_id?: string;
  grantor_id: string;
  grantor_name?: string;
  grantee_type: 'user' | 'unit' | string;
  grantee_id: string;
  grantee_name?: string;
  resource_type: GrantResourceType | string;
  resource_id: string;
  resource_name?: string;
  level: GrantLevel | string;
  reason: string | null;
  status: string;
  created_at: string;
  expires_at: string | null;
  revoked_at?: string | null;
}

export interface GrantBody {
  grantee_type: 'user' | 'unit';
  grantee_id: string;
  resource_type: GrantResourceType;
  resource_id: string;
  level: GrantLevel;
  reason?: string;
  expires_in_seconds?: number;
}

export interface HolderStats {
  documents?: number;
  memories?: number;
  questions_answered?: number;
  entities?: number;
  queue?: number;
  last_ingest_at?: string | null;
  status?: string;
  [key: string]: unknown;
}

export interface Holder {
  holder_id: string;
  owner_type: 'user' | 'unit' | string;
  owner_id: string;
  owner_name: string;
  name: string;
  status: string;
  mode: 'embedded' | 'external' | string;
  domains: string[];
  /** Domains of the holder's ingested records, as its heartbeats last reported them (routing uses both lists). */
  published_domains?: string[];
  export_policy: ExportPolicy;
  last_heartbeat_at: string | null;
  stats: HolderStats;
}

export interface HolderCreateBody {
  name: string;
  owner_type?: 'user' | 'unit';
  owner_id?: string;
  domains?: string[];
  export_policy?: ExportPolicy;
  mode?: 'embedded' | 'external';
}

export interface Document {
  doc_id: string;
  title: string;
  kind: string;
  source_root_id: string;
  observed_at: string | null;
  status: string;
  version: number;
  chars: number;
  chunks: number;
  domains: string[];
  created_at?: string;
  updated_at?: string;
}

export interface DocumentBody {
  title: string;
  text: string;
  kind?: string;
  observed_at?: string;
  domains?: string[];
  origin_id?: string;
}

export interface DocumentDetail {
  document: Document;
  text: string;
}

export interface MemoryResult {
  memory_id: string;
  text: string;
  kind: string;
  score?: number;
  observed_at?: string | null;
  sources?: unknown[];
  doc_id?: string | null;
  title?: string | null;
  holder_id?: string;
  holder_name?: string;
  created_at?: string;
}

export interface SearchResponse {
  results: MemoryResult[];
}

// ---------------------------------------------------------------- chat
export interface Chat {
  chat_id: string;
  agent_type: 'user' | 'unit' | string;
  agent_id: string;
  agent_name: string;
  title: string;
  updated_at: string;
}

export interface Citation {
  type: 'claim' | 'discovery' | 'evidence' | 'evidence_ref' | 'memory' | 'question' | 'goal' | string;
  id: string;
  label: string;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system' | string;
  content: string;
  citations: Citation[];
  at: string;
}

export interface ChatDetail {
  chat: Chat;
  messages: ChatMessage[];
}

export interface SendMessageResponse {
  message: ChatMessage;
  citations: Citation[];
  context_summary: { claims: number; discoveries: number; memories: number };
}

// ---------------------------------------------------------------- goals and loops
export interface Budget {
  tokens: number;
  usd: number;
  questions: number;
  followup_depth?: number;
}

export interface BudgetSpent {
  tokens: number;
  usd: number;
  questions: number;
}

export interface SuccessCriterion {
  metric: string;
  target: string | number;
  direction?: 'up' | 'down' | 'increase' | 'decrease' | string;
}

export interface Progress {
  known: boolean;
  value?: number;
  method?: string;
  computed_at?: string;
  note?: string;
}

export interface ActorRef {
  type: 'user' | 'unit' | 'system' | 'loop' | string;
  id: string;
  name: string;
}

export type GoalStatus = 'draft' | 'active' | 'paused' | 'completed' | 'archived' | string;

export interface Goal {
  goal_id: string;
  owner_type: 'user' | 'unit' | string;
  owner_id: string;
  owner_name: string;
  scope_unit_id: string | null;
  scope_unit_name: string | null;
  parent_goal_id: string | null;
  title: string;
  objective: string;
  success_criteria: SuccessCriterion[];
  /** metric -> value (numbers), or {note} for a free-text baseline */
  baseline: Record<string, number | string> | null;
  measurement_source: MeasurementSource | null;
  deadline: string | null;
  priority: number;
  status: GoalStatus;
  permitted_actions: string[];
  budget: Budget;
  budget_spent: BudgetSpent;
  dependencies: string[];
  progress: Progress;
  assignees: ActorRef[];
  is_demo: boolean;
  version: number;
  created_by: string;
  created_at: string;
  updated_at: string;
  loop?: Loop | null;
  counts?: { questions: number; open_questions: number; discoveries: number; claims: number };
}

export interface MeasurementSource {
  source?: string;
  metric?: string;
  domains?: string[];
  [key: string]: unknown;
}

export type LoopState = 'active' | 'waiting' | 'paused' | 'budget_exhausted' | 'blocked' | 'failed' | 'completed' | 'stopped' | string;

export interface Loop {
  goal_id: string;
  desired: 'active' | 'paused' | 'stopped' | string;
  state: LoopState;
  explanation: string;
  active_indicator: { active: boolean; heartbeat_age_seconds: number | null; worker_id: string | null };
  config: Json;
  last_run_at: string | null;
  next_check_at: string | null;
  run_count: number;
  stats: { questions_asked: number; discoveries: number; tokens: number; usd: number };
  budget_remaining: BudgetSpent;
}

export interface GoalCreateBody {
  title: string;
  objective: string;
  owner_type?: 'user' | 'unit';
  owner_id?: string;
  scope_unit_id?: string;
  parent_goal_id?: string;
  success_criteria?: SuccessCriterion[];
  /** "metric=52, other=3" or free text; the server stores {metric: number} or {note} */
  baseline?: string | Record<string, number | string>;
  measurement_source?: string | MeasurementSource;
  deadline?: string;
  priority?: number;
  permitted_actions?: string[];
  budget?: Partial<Budget>;
  dependencies?: string[];
  assignees?: ActorRef[];
  activate?: boolean;
}

export type GoalAction = 'activate' | 'pause' | 'resume' | 'complete' | 'archive' | 'assign' | 'decompose' | 'prioritize' | 'delegate';

export interface GoalActionBody {
  action: GoalAction;
  assignees?: ActorRef[];
  subgoals?: { title: string; objective: string; owner_type: string; owner_id: string; scope_unit_id?: string }[];
  priority?: number;
  owner_type?: string;
  owner_id?: string;
}

export interface Outcome {
  outcome_id: string;
  goal_id: string;
  kind: string;
  value: { metric: string; value: number | string; unit?: string; at?: string };
  claim_ids: string[];
  recorded_by?: ActorRef | string;
  created_at: string;
}

export interface OutcomeBody {
  kind: string;
  value: { metric: string; value: number | string; unit?: string; at?: string };
  claim_ids?: string[];
}

export interface GoalDetail {
  goal: Goal;
  subgoals: Goal[];
  parent: Goal | null;
  loop: Loop | null;
  questions: Question[];
  discoveries: Discovery[];
  outcomes: Outcome[];
  usage: { tokens: number; usd: number; calls: number };
}

export type LoopAction = 'start' | 'pause' | 'resume' | 'run_now' | 'stop';

// ---------------------------------------------------------------- questions
export interface LineageRef {
  type: string;
  id: string;
  label: string;
}

export interface PriorityBreakdown {
  method: 'heuristic' | string;
  goal_value: number;
  uncertainty: number;
  impact: number;
  missing_evidence: number;
  information_gain: number;
  cost: number;
  weights: PriorityWeights | Json;
}

export interface QuestionRoute {
  route_id: string;
  holder_id: string;
  holder_name: string;
  status: string;
  sent_at: string | null;
  responded_at: string | null;
  deadline_at: string | null;
}

export type QuestionStatus = 'draft' | 'open' | 'routed' | 'collecting' | 'evaluating' | 'resolved' | 'cancelled' | 'timeout' | string;

export interface Question {
  question_id: string;
  asker: ActorRef;
  goal_id: string | null;
  goal_title: string | null;
  text: string;
  kind: string;
  trigger: string;
  scope_unit_id: string | null;
  scope_unit_name: string | null;
  valid_from: string | null;
  valid_to: string | null;
  uncertainty: number | string | null;
  motivating_lineage: LineageRef[];
  candidate_domains: string[];
  policy: Json;
  budget: Partial<Budget> | Json;
  budget_spent: Partial<BudgetSpent> | Json;
  status: QuestionStatus;
  priority: number;
  priority_breakdown: PriorityBreakdown | null;
  parent_question_id: string | null;
  depth: number;
  cooldown_until: string | null;
  result: Json | string | null;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
  needs_my_input: boolean;
  routes: QuestionRoute[];
}

export interface QuestionCreateBody {
  text: string;
  goal_id?: string;
  scope_unit_id?: string;
  kind?: string;
  candidate_domains?: string[];
  valid_from?: string;
  valid_to?: string;
  policy?: Json;
  budget?: Partial<Budget>;
}

export interface Response {
  response_id: string;
  holder_id: string;
  holder_name: string;
  status: string;
  content: string;
  evidence: EvidenceRef[];
  confidence: number | null;
  freshness_at: string | null;
  received_at: string;
}

export interface RespondBody {
  content: string;
  holder_id?: string;
  doc_ids?: string[];
  no_evidence?: boolean;
}

export interface QuestionDetail {
  question: Question;
  responses: Response[];
  claims: Claim[];
  discoveries: Discovery[];
  lineage: Lineage | Json;
}

// ---------------------------------------------------------------- discoveries, claims, evidence, conflicts
export type ClaimStatus = 'hypothesis' | 'supported' | 'contested' | 'stale' | 'retracted' | string;

export interface ClaimSupport {
  independent_roots: number;
  copied_refs: number;
  unknown_independence: number;
  holders: string[];
  roots?: string[];
}

export interface Claim {
  claim_id: string;
  scope_unit_id: string | null;
  visibility: string;
  text: string;
  kind: string;
  status: ClaimStatus;
  confidence: number | null;
  valid_from: string | null;
  valid_to: string | null;
  version: number;
  supersedes_claim_id: string | null;
  superseded_by: string | null;
  goal_id: string | null;
  question_id: string | null;
  created_by: ActorRef;
  support: ClaimSupport;
  freshness_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface EvidenceRef {
  ref_id: string;
  holder_id: string;
  holder_name: string;
  source_root_id: string | null;
  root_known: boolean | number;
  kind: string;
  title: string;
  disclosed_excerpt: string | null;
  disclosure_level: string;
  observed_at: string | null;
  freshness_at: string | null;
  status: string;
  version: number;
  copies_of_same_root: number;
}

export interface EvidenceRawResponse {
  ref_id: string;
  holder_id: string;
  doc_id: string;
  title: string;
  text: string;
  observed_at: string | null;
  version: number;
}

export interface EvidenceDetail {
  evidence_ref: EvidenceRef;
  claims: Claim[];
}

export interface DiscoveryReview {
  user_id: string;
  user_name: string;
  action: string;
  note: string | null;
  at: string;
}

export interface DiscoverySupport {
  independent_roots: number;
  copied_refs: number;
  unknown_independence: number;
  holders: string[];
}

export type DiscoveryStatus = 'new' | 'reviewed' | 'accepted' | 'dismissed' | 'escalated' | string;

export interface Discovery {
  discovery_id: string;
  scope_unit_id: string | null;
  scope_unit_name: string | null;
  visibility: string;
  level: Level | string;
  kind: string;
  title: string;
  summary: string;
  claim_ids: string[];
  claims: Claim[];
  goal_id: string | null;
  goal_title: string | null;
  question_id: string | null;
  status: DiscoveryStatus;
  escalated_to: string | null;
  followup_question_ids: string[];
  reviews: DiscoveryReview[];
  support: DiscoverySupport;
  /** whether the evidence under the discovery is still current (sources revised, withdrawn or deleted degrade it) */
  evidence_health?: EvidenceHealth;
  freshness_at: string | null;
  is_demo: boolean;
  created_at: string;
  updated_at: string;
}

export interface EvidenceHealth {
  status: 'healthy' | 'degraded' | 'unsupported' | string;
  active_refs: number;
  inactive_refs: number;
  claims: Record<string, number>;
}

export type ReviewAction = 'reviewed' | 'accepted' | 'dismissed' | 'escalated' | 'comment';

export interface Revision {
  revision_id?: string;
  object_type?: string;
  object_id?: string;
  version?: number;
  action?: string;
  reason?: string | null;
  actor?: ActorRef | string | null;
  actor_id?: string | null;
  diff?: Json | null;
  at?: string;
  created_at?: string;
  [key: string]: unknown;
}

export interface Derivation {
  derivation_id?: string;
  claim_id?: string;
  operator?: string;
  model?: string | null;
  tier?: string | null;
  contributors?: (ActorRef | string)[];
  inputs?: unknown[];
  at?: string;
  created_at?: string;
  [key: string]: unknown;
}

export interface Lineage {
  nodes: NetworkNode[];
  edges: NetworkEdge[];
}

export interface DiscoveryDetail {
  discovery: Discovery;
  claims: Claim[];
  evidence: EvidenceRef[];
  conflicts: Conflict[];
  lineage: Lineage;
  followups: Question[];
  revisions: Revision[];
}

export interface ClaimDependencies {
  roots: { source_root_id: string; ref_count: number; holders: string[] }[];
  unknown: string[];
}

export interface ClaimFreshness {
  freshness_at: string | null;
  age_days: number | null;
  stale: boolean;
}

export interface ClaimDetail {
  claim: Claim;
  evidence: EvidenceRef[];
  derivations: Derivation[];
  conflicts: Conflict[];
  revisions: Revision[];
  history: Claim[];
  dependencies: ClaimDependencies;
  freshness: ClaimFreshness;
}

export interface Conflict {
  conflict_id: string;
  claim_a: Claim;
  claim_b: Claim;
  status: string;
  summary: string;
  investigation: unknown[];
  resolution: string | Json | null;
  question_id: string | null;
  created_at: string;
  resolved_at: string | null;
}

export type ConflictOutcome = 'a_wins' | 'b_wins' | 'both_valid_scoped' | 'both_retracted' | 'unresolved';

// ---------------------------------------------------------------- workspaces
export interface EmployeeWorkspace {
  goals: Goal[];
  questions_needing_input: Question[];
  discoveries: Discovery[];
  holders: Holder[];
  recent_memory: MemoryResult[];
  shared: Grant[];
  loop_states: Loop[];
}

export interface SynthesisItem {
  text: string;
  discovery_ids?: string[];
  claim_ids?: string[];
  conflict_ids?: string[];
  unit_names?: string[];
}

export interface Synthesis {
  summary: string;
  recurring_problems: SynthesisItem[];
  constraints: SynthesisItem[];
  conflicting_findings: SynthesisItem[];
  opportunities: SynthesisItem[];
  escalations: SynthesisItem[];
  material_uncertainties: SynthesisItem[];
  decisions_needed: SynthesisItem[];
  computed_at: string | null;
  method: 'model' | 'none' | string;
}

export interface Dependency {
  goal_id: string;
  depends_on: string;
  status: string;
  goal_title?: string;
  depends_on_title?: string;
}

export interface ChildSummary {
  unit: Unit;
  discoveries: number;
  open_questions: number;
  goals: number;
}

export interface Comparison {
  unit_id: string;
  name: string;
  discoveries: number;
  supported_claims: number;
  open_conflicts: number;
  stale_claims: number;
}

export interface UnitWorkspace {
  unit: Unit;
  level: Level | string;
  goals: Goal[];
  discoveries: Discovery[];
  open_questions: Question[];
  conflicts: Conflict[];
  dependencies: Dependency[];
  progress: { goal_id: string; progress: Progress }[];
  synthesis: Synthesis | null;
  children: ChildSummary[];
  members?: UnitMember[];
  comparisons: Comparison[];
}

export interface Decision {
  text: string;
  discovery_ids?: string[];
  claim_ids?: string[];
  conflict_ids?: string[];
  kind?: string;
  [key: string]: unknown;
}

export interface ExecutiveWorkspace extends UnitWorkspace {
  strategic: Discovery[];
  material_uncertainties: Claim[];
  decisions: Decision[];
}

// ---------------------------------------------------------------- admin
export interface Worker {
  worker_id: string;
  role: string;
  hostname: string;
  last_heartbeat_at: string | null;
  heartbeat_age_seconds: number | null;
  busy: boolean;
  stats: Json;
}

export interface Job {
  job_id: string;
  kind: string;
  status: string;
  attempts?: number;
  max_attempts?: number;
  last_error?: string | null;
  error?: string | null;
  payload?: Json;
  created_at?: string;
  updated_at?: string;
  run_at?: string | null;
  leased_by?: string | null;
  [key: string]: unknown;
}

export interface UsageBucket {
  calls: number;
  tokens: number;
  usd: number;
}

export interface AdminOverview {
  workers: Worker[];
  jobs: { queued: number; leased: number; done: number; dead: number; failed: number };
  failed_jobs: Job[];
  holders: Holder[];
  usage: { today: UsageBucket; month: UsageBucket; by_tier: Record<string, UsageBucket> };
  budgets: { goal_id: string; title: string; budget: Budget; spent: BudgetSpent }[];
  loops: Loop[];
  deployment: {
    version: string;
    settings: Json;
    migrations: { current: string | number | null; latest: string | number | null; pending: number | string[] };
    transport: string | Json;
    uptime_seconds: number;
  };
  metrics: {
    queue_backlog: number;
    failed_routes: number;
    question_outcomes: Record<string, number>;
    evidence_freshness: { stale: number; fresh: number; median_age_days: number | null };
  };
}

export interface AuditRow {
  id: number;
  tenant_id: string;
  at: string;
  actor_type: string;
  actor_id: string | null;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  outcome: string;
  detail: Json | null;
  request_id: string | null;
}

export interface UsageRow {
  at?: string;
  day?: string;
  task?: string;
  tier?: string;
  provider?: string;
  model?: string;
  calls?: number;
  tokens?: number;
  usd?: number;
  [key: string]: unknown;
}

export interface ModelsResponse {
  tiers: Record<string, { provider: string; model: string }>;
  embedding: { provider: string; model: string };
  prices: Json;
  policy_tiers: Record<string, string>;
}

export interface Notification {
  id: number;
  kind: string;
  title: string;
  body: string;
  ref_type: string | null;
  ref_id: string | null;
  at: string;
  read_at: string | null;
}

// ---------------------------------------------------------------- lists and errors
export interface ListResponse<T> {
  items: T[];
  total?: number;
}

export interface ApiErrorBody {
  error: string;
  code: string;
  existing_question_id?: string;
  [key: string]: unknown;
}

// ---------------------------------------------------------------- SSE
export type EventKind =
  | 'goal.updated'
  | 'loop.state'
  | 'question.created' | 'question.routed' | 'question.responded' | 'question.resolved'
  | 'discovery.created' | 'discovery.updated'
  | 'claim.committed' | 'claim.revised' | 'claim.stale' | 'claim.retracted'
  | 'conflict.opened' | 'conflict.resolved'
  | 'holder.status'
  | 'document.ingested' | 'document.revised' | 'document.retracted'
  | 'job.progress'
  | 'membership.changed'
  | 'grant.changed'
  | 'org.changed'
  | 'policy.updated'
  | 'notification';

export const EVENT_KINDS: EventKind[] = [
  'goal.updated', 'loop.state',
  'question.created', 'question.routed', 'question.responded', 'question.resolved',
  'discovery.created', 'discovery.updated',
  'claim.committed', 'claim.revised', 'claim.stale', 'claim.retracted',
  'conflict.opened', 'conflict.resolved',
  'holder.status',
  'document.ingested', 'document.revised', 'document.retracted',
  'job.progress', 'membership.changed', 'grant.changed', 'org.changed', 'policy.updated', 'notification',
];

export interface LiveEvent {
  id: string;
  tenant_id: string;
  kind: EventKind | string;
  ref_type: string | null;
  ref_id: string | null;
  payload: Json;
  at: string;
}

// ---------------------------------------------------------------- integrations (docs/mycelic/INGESTION.md §13.2)
export type ConnectorStatusLabel = 'scaffold' | 'implemented' | 'tested-offline' | 'live-verified' | string;

export interface ConnectorCatalogItem {
  connector_type: string;
  display_name: string;
  version: string;
  status: ConnectorStatusLabel;
  auth_kinds: string[];
  modes: string[];
  source_types: string[];
  scopes: { scope: string; required: boolean; reason: string }[];
  capabilities: Record<string, unknown>;
  terms_notes: string;
  ownership: string[];
  enabled_for_tenant: boolean;
  connectable: boolean;
}

export interface Connector {
  connector_id: string;
  connector_type: string;
  display_name: string;
  account_label?: string | null;
  source_account_id?: string | null;
  auth_kind: string;
  ownership: string;
  mode: string;
  status: string;
  status_code?: string | null;
  granted_scopes: string[];
  created_at?: string;
  updated_at?: string;
  last_sync_at?: string | null;
  last_success_at?: string | null;
  health?: Record<string, unknown>;
  config?: Record<string, unknown>;
  counts?: { sources_included: number; sources_pending: number; sources_excluded: number; records: number };
  warnings?: string[];
}

export interface ConnectorSource {
  source_id: string;
  connector_id: string;
  source_type: string;
  external_id: string;
  name: string;
  selection: 'included' | 'excluded' | 'pending_review' | string;
  selection_reason: string;
  visibility: 'public' | 'members' | 'private' | string;
  exportable: boolean;
  disclosure: string | null;
  default_domain_ids: string[];
  sensitivity: string;
  access_state: string;
  members: number;
}

export interface SyncReport {
  streams: string[];
  pages: number;
  raw_items: number;
  enqueued: number;
  duplicates: number;
  excluded: number;
  normalize_errors: number;
  error_code: string | null;
  yielded: boolean;
}

export interface AdminConnectorRow {
  connector_id: string;
  holder_id: string;
  holder_name: string;
  scope: 'personal' | 'organization' | string;
  connector_type: string;
  status: string;
  status_code: string;
  mode: string;
  sources_included: number;
  sources_pending: number;
  records: number;
  last_sync_at: string | null;
  last_success_at: string | null;
  created_at: string;
  granted_scopes: string[];
}

export interface TaxonomyDomain {
  domain_id: string;
  parent_id: string | null;
  name: string;
  path: string;
  description: string;
  status: 'active' | 'deprecated' | string;
}

export interface TaxonomyResponse {
  taxonomy_version: number;
  configured: boolean;
  items: TaxonomyDomain[];
  aliases: { alias: string; domain_id: string }[];
}

export interface DomainMembership {
  domain_id: string;
  path: string;
  confidence: number;
  method: string;
  is_primary: boolean;
  personal: boolean;
  model_version?: string;
  taxonomy_version?: number;
  evidence?: Record<string, unknown>;
  corrected_by?: string | null;
  updated_at?: string;
}

export interface IngestRecordRow {
  record_id: string;
  kind: string;
  source_app: string;
  object_type: string;
  created_at: string | null;
  visibility: string;
  title: string;
  /** The beginning of the record's text (readers the source ACL admits only). */
  snippet?: string;
  domains: DomainMembership[];
}

export interface IngestRecordDetail {
  record: IngestRecordRow & { source_root_id: string | null; root_method: string };
  domains: DomainMembership[];
  history: { domain_id: string; action: string; method: string; actor_type: string; reason: string; at: string; before: Record<string, unknown>; after: Record<string, unknown> }[];
  edges: { subject: string; predicate: string; object: string; modality: string; confidence: number; status: string }[];
  entities: string[];
}
