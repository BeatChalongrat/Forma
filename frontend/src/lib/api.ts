/** Typed API client. All calls go through the Vite proxy (/api → FastAPI). */
import { authHeaders, login } from './auth'

export class ApiError extends Error {
  status: number
  code: string
  details: unknown
  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message)
    this.status = status
    this.code = code
    this.details = details
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { ...init, headers: { ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }), ...authHeaders(), ...(init?.headers ?? {}) } })
  if (res.status === 401 && !path.startsWith('/api/auth/')) {
    void login()
  }
  if (!res.ok) {
    let body: { error?: string; message?: string; detail?: unknown; details?: unknown } = {}
    try {
      body = await res.json()
    } catch {
      /* no body */
    }
    throw new ApiError(res.status, body.error ?? 'error', body.message ?? (typeof body.detail === 'string' ? body.detail : res.statusText), body.details ?? body.detail)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

const get = <T>(p: string) => request<T>(p)
const post = <T>(p: string, body?: unknown) => request<T>(p, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
const del = <T>(p: string) => request<T>(p, { method: 'DELETE' })
const put = <T>(p: string, body: unknown) => request<T>(p, { method: 'PUT', body: JSON.stringify(body) })
const patch = <T>(p: string, body: unknown) => request<T>(p, { method: 'PATCH', body: JSON.stringify(body) })

export type ChatStreamEvent = { type: 'started'; request_id: string } | { type: 'phase'; phase: 'research' | 'coding' | 'writing' } | { type: 'delta'; text: string } | { type: 'reset' } | { type: 'done'; turn: ChatTurn } | { type: 'error'; message: string }
async function chatFile(id: string): Promise<Blob> {
  const res = await fetch(`/api/assistant/attachments/${encodeURIComponent(id)}`, { headers: authHeaders() })
  if (!res.ok) throw new ApiError(res.status, 'error', res.statusText)
  return res.blob()
}

function attachChatFile(id: string, file: File): Promise<ChatFile> {
  const body = new FormData()
  body.append('file', file)
  return request<ChatFile>(`/api/assistant/conversations/${encodeURIComponent(id)}/attachments`, { method: 'POST', body })
}

async function chatStream(id: string, body: { question: string; request_id: string; language: 'th' | 'en'; timezone: string; attachment_ids?: string[]; artifact_id?: string; target?: string }, onEvent: (event: ChatStreamEvent) => void, signal: AbortSignal): Promise<ChatTurn> {
  const response = await fetch(`/api/assistant/conversations/${encodeURIComponent(id)}/messages/stream`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify(body), signal })
  if (response.status === 401) void login()
  if (!response.ok || !response.body) throw new ApiError(response.status, 'stream_failed', 'Could not start reply')
  const reader = response.body.getReader(), decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      buffer += decoder.decode(value, { stream: !done })
      let end: number
      while ((end = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, end).trimEnd(); buffer = buffer.slice(end + 1)
        if (!line.startsWith('data:')) continue
        const event = JSON.parse(line.slice(5)) as ChatStreamEvent
        if (event.type === 'error') throw new Error(event.message)
        onEvent(event)
        if (event.type === 'done') return event.turn
      }
      if (done) throw new Error('Reply stream interrupted')
    }
  } finally { await reader.cancel().catch(() => undefined); reader.releaseLock() }
}

// ---------------------------------------------------------------------------------------------
// Types (mirror backend serializers)
// ---------------------------------------------------------------------------------------------
export type Customer = {
  id: string; code: string; name: string; erp_type: string | null; notes: string | null; active: boolean; default_target_schema: string | null; policy_overrides?: Record<string, unknown> | null; created_at: string
  rounds?: number | ImportRound[]; last_round_at?: string | null; has_approved_mapping?: boolean; next_expected?: Cadence
  mappings?: MappingVersion[]; overrides?: Override[]
}
export type Override = { id: string; target: string; where: Record<string, unknown>; value: unknown; note: string | null; author: string; created_at: string }
export type SourceFile = {
  id: string; round_id: string; name: string; size: number; kind: 'excel' | 'csv'; status: string; sha256: string; duplicate_of: string | null; error: string | null
  created_at: string; fingerprint: string | null; drift: DriftReport | null; tables: number; sheets: string[]; profile?: WorkbookProfile
}
export type DriftReport = { classification: string; similarity: number; changes: Record<string, unknown>[]; renamed: Record<string, string>; added: string[]; removed: string[]; matches_other_customer?: { customer_code: string } }
export type ColumnProfile = { name: string; position: number; inferred_type: string; null_ratio: number; unique_ratio: number; unique_count: number; samples: string[]; min: string | null; max: string | null; header_lang: string; hints: string[] }
export type TableProfile = { sheet: string; sheet_index: number; file_id?: string; file_name?: string; table: { header_row: number; headers: string[]; n_rows: number; title: string | null; score: number }; columns: ColumnProfile[]; n_rows: number; key_candidates: string[] }
export type WorkbookProfile = { meta: { file_name: string; kind: string; sheets: { name: string; index: number; hidden: boolean; n_rows: number; n_cols: number; merged_ranges: string[]; formula_cells: number }[] }; tables: TableProfile[] }
export type RoundStatus = 'uploaded' | 'profiled' | 'review' | 'auto_approved' | 'approved' | 'transformed' | 'approved_data' | 'published' | 'blocked' | 'rejected' | 'failed'
export type ImportRound = {
  uploaded_at?: string
  id: string; customer_id: string; label: string | null; status: RoundStatus; target_schema: string; notes: string | null
  summary: { drift?: string | null; drift_report?: DriftReport | null; decision?: string; mapping_version_id?: string; dataset_version_id?: string; rows?: number; errors?: number; error?: string; simulation?: SimSummary | null; has_previous_approval?: boolean; batch_id?: string; auto_publish?: boolean; publish?: PublishDecision; verdict?: Verdict | null; format_memory?: FormatMemory }
  created_at: string; updated_at: string; files?: SourceFile[]; customer?: { id: string; code: string; name: string }
  mappings?: MappingVersion[]; datasets?: DatasetVersion[]; review_tasks?: ReviewTask[]; jobs?: Job[]
}
/** what the policy decided after the data was created, for rounds that publish automatically (ADR-027) */
export type PublishDecision = { decision: 'publish' | 'review'; reasons: string[]; export_id?: string }
/** the verdict layer (ADR-036): one conclusion per file, from the stored reports */
export type Bilingual = { en: string; th: string }
export type VerdictOutcome = 'accept' | 'assign_customer' | 'answer' | 'fix_mapping' | 'check_data' | 'fix_data' | 'reject_file' | 'wait'
export type VerdictBrief = { conclusion: Bilingual; outcome: VerdictOutcome; confidence: number; word: 'sure' | 'likely' | 'unsure'; rules_fired: string[]; digest: string; stage: 'mapping' | 'data' }
export type VerdictDecision = { id: string; kind: 'boolean' | 'number' | 'confirm'; semantic?: string | null; question: Bilingual; why: Bilingual; options: unknown[]; default: unknown; default_reason?: Bilingual; evidence_ids: number[] }
export type Verdict = VerdictBrief & {
  subject: { kind: 'mapping' | 'dataset'; id: string; round_id: string }
  must_decide: VerdictDecision[]
  evidence: { id: number; kind: string; text: Bilingual; numbers: Record<string, unknown>; link: string | null }[]
  suppressed: { count: number; groups: { reason: string; text: Bilingual; pointers: string[] }[] }
  policy_decision: string | null; rules_version: string; narrative: { text: string; model: string } | null
  round_status?: string; mapping_status?: string; dataset_status?: string
}
/** what human decisions taught FORMA (ADR-038) */
export type LearnedItem = { id: string; customer_id: string | null; kind: 'alias' | 'constant' | 'promoted'; target: string | null; header: string | null; column: string | null; value: unknown; actor: string; round_id: string | null; uses: number; created_at: string | null }
export type LearningMonth = { month: string; mappings_approved: number; accepted_as_proposed: number; corrections: number; corrections_per_approval: number | null; learned_fields_used: number }
export type LearningStats = { series: LearningMonth[]; aliases: number; constants: number; promoted: number; customers_taught: number; uses: number }
export type LearningSuggestion = { header: string; target: string; field: FieldSpec; customers: number }
export type LearningView = { stats: LearningStats; learned: { total: number; items: LearnedItem[] }; suggestions: LearningSuggestion[] }
/** policy calibration from outcomes (ADR-037) */
export type OutcomeCounts = { n: number; right: number; precision: number | null; accepted_unchanged?: number; corrected?: number; rejected?: number; auto_ok?: number; auto_then_edited?: number; auto_then_rolled_back?: number }
export type CalibrationReport = { since: string; decisions: number; per_rule: Record<string, OutcomeCounts>; per_decision: Record<string, OutcomeCounts>; customers: (OutcomeCounts & { customer_id: string; code: string; auto: number; review: number })[] }
export type Proposal = { id: string; digest: string; scope: 'policy' | 'customer'; customer_id?: string; customer_code?: string; path: string; value: unknown; current: unknown; title: Bilingual; why: Bilingual; saves: number; evidence: string[] }
/** proactive intelligence (ADR-039) */
export type Cadence = { status: 'overdue' | 'due' | 'ahead' | 'unknown'; rounds: number; every_days?: number; last?: string | null; expected?: string; late_days?: number; grace_days?: number }
export type ExpectedDelivery = Cadence & { customer_id: string; code: string; name: string; text: Bilingual }
export type WatchItem = { customer_id: string; code: string; name: string; reasons: Bilingual[]; text: Bilingual; last_round_id: string }
export type Foresight = { generated_at: string; expected: ExpectedDelivery[]; counts: Record<Cadence['status'], number>; watchlist: WatchItem[] }
export type VerdictAccepted = { did: 'approve_mapping' | 'apply_answers' | 'approve_dataset'; mapping_id?: string; dataset_id?: string; auto_publish?: boolean; verdict: Verdict }
export type BatchMatch = { kind: 'known' | 'unknown'; how?: 'exact' | 'same_columns' | 'similar' | 'file_name'; named?: boolean; customer_id?: string; customer_code?: string | null; similarity?: number | null; classification?: string; reason?: string; candidates?: { customer_id: string; customer_code: string | null; similarity: number | null }[] }
export type BatchItemState = 'processing' | 'needs_customer' | 'needs_review' | 'needs_data_check' | 'ready' | 'failed'
export type FormatMemory = { status: 'known' | 'reused' | 'changed' | 'new' | 'edited'; mapping_id: string | null; mapping_version: number | null; target_schema: string }
export type BatchItem = {
  id: string; file_name: string; size: number; match: BatchMatch | null; customer: { id: string; code: string | null; name: string | null } | null
  round_id: string | null; round_status: string | null; mapping_version_id: string | null; dataset_version_id: string | null
  publish: PublishDecision | null; export_id: string | null; json_name: string; rows: number | null
  state: BatchItemState; step: number; message: string; reasons?: string[]; verdict?: VerdictBrief | null
  format_memory?: FormatMemory | null
}
export type BatchSummary = { id: string; label: string | null; work_date: string | null; expected_files: number | null; complete: boolean; auto_publish: boolean; created_by: string; created_at: string; total: number; counts: Partial<Record<BatchItemState, number>>; done: boolean; stuck: number }
export type Batch = BatchSummary & { items: BatchItem[] }
export type SimSummary = { rows_in: number; rows_out: number; findings: { error: number; warning: number; info: number }; duration_ms?: number }
export type Finding = { kind: string; severity: 'error' | 'warning' | 'info'; message: string; target: string | null; row: number | null; identity: string | null; details: Record<string, unknown> }
export type Expr = { op: string; name?: string; source?: string | null; value?: unknown; args?: Expr[]; params?: Record<string, unknown> }
export type FieldMapping = { target: string; expr: Expr; confidence?: number | null; evidence?: string[]; note?: string | null }
export type SourceSpec = { id: string; role: string; file: { name_pattern?: string | null; sheet_names_any?: string[] }; sheet?: { name?: string; index?: number; match?: string } | null; table: Record<string, unknown>; csv: Record<string, unknown>; rename: Record<string, string> }
export type CollectionMapping = { target: string; source: string; link: { left: string; right: string }[]; fields: FieldMapping[]; filter?: Expr | null; dedupe_keys?: string[] }
export type MappingDocument = {
  meta: { mapping_id?: string | null; version: number; customer_id?: string | null; target_schema: string; created_by: string; model?: string | null; confidence?: number | null; evidence: string[]; fingerprints?: Record<string, string>; notes?: string | null }
  sources: SourceSpec[]; joins: Record<string, unknown>[]; record: { root_source: string; filters: Expr[]; dedupe: { keys: string[]; keep: string } }
  fields: FieldMapping[]; collections: CollectionMapping[]; quality: Record<string, unknown>[]; provenance: boolean; attributes_passthrough: boolean
}
export type SandboxSide = { ok: boolean; problems?: string[]; rows_in?: number; rows_out?: number; errors?: number; warnings?: number; schema_errors?: number; duplicates?: number; missing_identity?: number; invalid_prices?: number }
export type SandboxDelta = { lost: number; gained: number; changed_records: number; changed_prices: number; large_price_moves: number; new_errors: number; new_invalid_prices: number; new_duplicates: number; override_conflicts?: number }
export type OverrideConflict = { override_id: string | null; target: string; identity?: string; kind: 'no_record' | 'value_changed' | 'scope_changed' | 'field_dropped'; value?: unknown; before?: unknown; after?: unknown; message: string }
export type SandboxItem = { identity: string; pointer?: string; before?: unknown; after?: unknown; pct?: number | null; changes?: { pointer: string; before: unknown; after: unknown }[]; fields?: number }
export type SandboxReport = {
  rows_limit: number | null; old: SandboxSide; new: SandboxSide; delta: SandboxDelta | null
  baseline_id: string | null; baseline_version?: number; verdict?: 'pass' | 'fail' | 'n/a'; problems?: string[]; message?: string
  details?: Record<'price_changes' | 'changed' | 'lost' | 'gained', SandboxItem[]>
  override_conflicts?: OverrideConflict[]
}
export type RiskLevel = 'low' | 'medium' | 'high'
export type PolicyDecision = { decision: 'auto_approve' | 'review' | 'block'; priority: number; reasons: string[]; risk_score: number; risk_level?: RiskLevel; risk_factors?: string[]; impact?: { affected_records: number; of_records: number; ratio: number | null; changed_prices: number; high?: boolean }; checks: Record<string, unknown>; source?: string; simulation?: SimSummary | null; sandbox?: SandboxReport | null; field_review?: FieldReview | null }
export type DataCheck = { verdict: 'same' | 'vat_added' | 'vat_removed' | 'scaled' | 'different' | 'no_data'; summary: string; pairs: number; equal_share?: number; ratio?: number; examples?: { identity: string; before: unknown; after: unknown }[] }
export type FieldChange = {
  pointer: string; status: 'unchanged' | 'changed' | 'added' | 'removed'; risk: string | null; check: DataCheck | null
  before: { expr: Expr; reads: string[]; collection?: string | null } | null
  after: { expr: Expr; reads: string[]; collection?: string | null; confidence: number; evidence: string[] } | null
}
export type FieldReview = { baseline_id: string | null; baseline_version?: number; compared_with?: string | null; counts: Partial<Record<FieldChange['status'], number>>; needs_decision: string[]; fields: FieldChange[] }
export type RelNode = { id: string; sheet: string | null; file: string | null; role: string; root: boolean; used: boolean; collections: string[]; rows: number; columns: { name: string; key: boolean; mapped: boolean }[]; source_spec?: SourceSpec }
export type RelEdge = {
  from: { node: string; column: string }; to: { node: string; column: string }; kind: 'link' | 'join' | 'suggested'; used: boolean; collection?: string; expected?: string; missing?: boolean
  overlap?: number; cardinality?: string; from_match?: number; to_match?: number; from_orphan_rows?: number; to_orphan_rows?: number; from_unique?: boolean; to_unique?: boolean; score?: number; evidence?: string[]; warnings: string[]
}
export type Relationships = { root: string; rows_limit: number | null; nodes: RelNode[]; edges: RelEdge[]; problems: string[]; probe?: RelEdge }
export type RelEnd = { node: string; column: string }
export type MappingVersion = {
  id: string; customer_id: string; round_id: string | null; version: number; status: 'proposed' | 'approved' | 'rejected' | 'superseded'; target_schema: string
  confidence: number | null; created_by: string; model: string | null; policy: PolicyDecision; open_questions: string[]; validation: { ok: boolean; issues: { level: string; code: string; message: string; path: string }[] } | null
  parent_id: string | null; approved_by: string | null; approved_at: string | null; comment: string | null; created_at: string; agent_log: Record<string, unknown>[]; sample: Record<string, unknown>[]; document?: MappingDocument
}
export type ReviewTask = {
  explanations?: { en: string; th: string }[]
  id: string; round_id: string; customer_id: string; mapping_version_id: string | null; dataset_version_id: string | null; kind: string; priority: number; status: string
  reasons: string[]; details: { decision?: string; risk_level?: RiskLevel; risk_factors?: string[]; impact?: PolicyDecision['impact']; open_questions?: string[]; findings?: Finding[]; verdict?: VerdictBrief }; resolver: string | null; comment: string | null; created_at: string; resolved_at: string | null
  customer?: { id: string; code: string; name: string } | null; round?: { id: string; label: string | null; status: string } | null
}
export type DatasetVersion = {
  id: string; round_id: string; customer_id: string; mapping_version_id: string; version: number; status: string; row_count: number
  /** records that do not match the target format: approval and export are refused until it is 0 (ADR-026) */
  invalid_records: number
  stats: Record<string, unknown>; findings: Finding[]; parent_id: string | null; created_by: string; note: string | null; created_at: string
  change_sets?: ChangeSet[]; exports?: ExportArtifact[]; history?: DatasetVersion[]
}
export type ChangeSet = { id: string; dataset_version_id: string; result_version_id: string | null; kind: string; operations: Operation[]; impact: Impact; author: string; note: string | null; status: string; created_at: string }
export type ExportArtifact = { id: string; dataset_version_id: string; fmt: string; row_count: number; sha256: string; created_at: string; path: string }
export type Job = {
  id: string; kind: string; status: 'queued' | 'running' | 'done' | 'failed'; progress: number; message: string | null; result?: Record<string, unknown> | null; error?: string | null
  round_id?: string | null; batch_id?: string | null; attempts?: number; worker?: string | null; created_at?: string; started_at?: string | null; finished_at?: string | null; duration_ms?: number | null
}
export type BatchStatus = { batch_id: string; total: number; by_status: Record<string, number>; finished: number; progress: number; done: boolean; jobs: { id: string; round_id: string | null; status: string; progress: number; error: string | null; duration_ms: number | null }[] }
export type QueueStats = { by_status: Record<string, number>; queued: number; running: number; failed: number; oldest_queued_age_s: number | null; limit: number; backend: string; workers?: number; inflight?: number; nodes?: number; error?: string }
export type Ready = { status: 'ok' | 'degraded'; checks: Record<string, { ok: boolean; detail?: string }> }
export type Me = { subject: string; name: string; roles: string[]; effective_roles: string[]; customers: string[] | null; mode: string }
export type OpsSummary = {
  queue: QueueStats; ready: Ready; review: { open: number; oldest_age_s: number | null }
  failed_jobs: (Job & { customer_code?: string | null })[]
  llm_spend_by_day: { day: string; calls: number; tokens: number; cost_usd: number }[]
  slowest_rounds: { job_id: string; kind: string; round_id: string; customer_code: string | null; label: string | null; duration_ms: number | null; stages: { profile?: number | null; propose?: number | null; transform?: number | null } }[]
  breaker: { open: boolean; failures: number; opened_at?: string | null; backups?: Record<string, boolean> } | null
  storage: string; database: string; auth: string; tracing: boolean; schema_revision: string | null
}
export type FieldSpec = { pointer: string; name: string; json_type: string[]; semantic: string | null; risk: 'critical' | 'high' | 'medium' | 'low'; required: boolean; nullable: boolean; enum: unknown[] | null; format: string | null; synonyms_en: string[]; synonyms_th: string[]; description: string | null; description_th: string | null; in_collection: string | null; is_collection: boolean; system: boolean; default: unknown }
export type SchemaSummary = { schema_id: string; version: number; ref: string; entity: string; status: string; title: string; description: string; identity_keys: string[]; field_count: number; fields?: FieldSpec[]; document?: unknown; source?: 'ui' | 'file'; is_latest?: boolean; is_default?: boolean }
export type SchemaPreview = SchemaSummary & { fields: FieldSpec[]; findings: { level: 'warning' | 'info'; pointer: string; message: string }[]; previous: string | null; diff: { added: string[]; removed: string[]; changed: { pointer: string; changes: Record<string, unknown> }[] } | null }
export type BackupKey = {
  vision: boolean | null
  id: string; provider: string; model: string; base_url: string | null; api_key_hint: string | null
  usable: boolean; circuit_open: boolean; warning: string | null; added_by?: string; added_at?: string
}
export type AISettings = {
  provider: string; model: string; base_url: string | null; timeout_s: number; max_retries: number
  api_key_set: boolean; api_key_hint: string | null; api_key_source: 'ui' | 'env' | null; sources: Record<string, 'ui' | 'server'>
  active_provider: string; active_model: string; active_route: string | null; offline: boolean; warning: string | null; providers: string[]
  /** known model names per provider (any other name can still be typed) */
  models: Record<string, string[]>
  circuit_open: boolean; backups: BackupKey[]; max_backups: number; vision: boolean | null; supports_vision: boolean
  updated_by?: string; updated_at?: string
}
export type AiProbe = { ok: boolean; offline?: boolean; message: string; model?: string; provider?: string; latency_ms?: number }
export type AISettingsPatch = Partial<{ provider: string | null; model: string | null; base_url: string | null; timeout_s: number | null; max_retries: number | null; api_key: string | null }>
export type Policy = {
  auto_approve: { min_mapping_confidence: number; min_field_confidence_critical: number; min_field_confidence_high: number; max_warning_findings: number; allow_errors: boolean; allowed_drift: string[]; require_previous_approval_for_auto: boolean; max_row_change_ratio: number }
  review: { priority_weights: Record<string, number>; high_impact_ratio?: number }
  block: Record<string, boolean>
  risk_overrides: Record<string, string>
  sandbox: { enabled: boolean; max_lost_records: number; max_new_errors: number; max_new_invalid_prices: number; max_new_duplicates: number; max_changed_prices_auto: number; trust_data_checks?: boolean }
  publish?: { enabled: boolean; max_high_anomalies: number; max_error_findings: number }
  anomaly?: { max_price_change_pct: number; max_discount_pct: number; max_stock_drop_pct: number; max_removed_pct: number; row_count_tolerance_pct: number; outlier_mad: number; history_rounds: number; block_approval_on_high: boolean }
  [k: string]: unknown
}
export type PolicyView = { effective: Policy; file: Policy; source: 'ui' | 'file'; updated_by?: string; updated_at?: string }
export type Operation = { kind: 'set_cell' | 'set_field' | 'scale_field' | 'delete_rows' | 'delete_row' | 'set_item'; target?: string; identity?: string; value?: unknown; factor?: number; where?: Expr | null; identities?: string[]; explanation?: string }
export type Impact = { rows_affected: number; cells_changed: number; rows_deleted: number; fields: string[]; sample: { row: number; identity: string; target: string | null; before: unknown; after: unknown }[] }
export type RowsPage = { total: number; offset: number; limit: number; identity_keys: string[]; rows: Record<string, unknown>[]; columns: FieldSpec[]; findings: Finding[] }
export type Dashboard = {
  customers: number; rounds_by_status: Record<string, number>; open_reviews: number; approved_mappings: number; auto_approved_mappings: number; mapping_versions: number; reused_mappings: number
  published_rows: number; failed_jobs: number; llm: { calls: number; cost_usd: number; tokens: number; provider: string; model: string }; recent_rounds: ImportRound[]; automation_rate: number | null; learning?: { aliases: number; constants: number; uses: number; corrections_per_approval: number | null }
}
export type ChangeReport = {
  has_previous: boolean; current_rows: number; previous_rows?: number; previous_dataset_id?: string; added?: string[]; removed?: string[]; added_count?: number; removed_count?: number; changed_count?: number
  changed?: { identity: string; changes: { pointer: string; before: unknown; after: unknown }[] }[]; price_changes?: { identity: string; before: number; after: number; pct: number | null }[]; price_change_count?: number; large_price_moves?: { identity: string; before: number; after: number; pct: number | null }[]; promotion_changes?: number
  anomalies?: AnomalyReport
}
export type Anomaly = { identity?: string; kind: string; severity: 'high' | 'medium' | 'low'; message: string; pointer?: string; value?: unknown; expected?: unknown; score?: number }
export type AnomalyReport = { records: number; anomalous_records: number; normal_pct: number; high: number; medium: number; low: number; by_kind: Record<string, number>; dataset: Anomaly[]; items: Anomaly[]; baseline: { rounds: number; avg_rows: number; current_rows: number } | null; headline: string }
export type SourceCell = { source: string; file: string | null; sheet: string | null; row: number | null; column: string | null; header: string; raw: unknown; verified: boolean | null }
export type TraceField = { pointer: string; field: string; item: number | null; value: unknown; mapped: unknown; expr: Expr | null; confidence?: number; evidence?: string[]; cells: SourceCell[] }
export type TraceStep = { dataset_id: string; version: number; kind: string; by: string; at: string; note: string | null; present: boolean; changes: { pointer: string; before: unknown; after: unknown }[] }
export type OverrideRule = { id: string; target: string; where: Record<string, unknown>; value: unknown; author: string; note: string | null; created_at: string | null }
export type Cause = { kind: 'no_baseline' | 'new_record' | 'unchanged' | 'manual' | 'source_changed' | 'mapping_changed' | 'previous_edit' | 'unknown'; summary: string; evidence?: string[] }
export type Trace = {
  identity: string; dataset_id: string; version: number; mapping_version_id: string; mapping_version: number
  source: { file: string | null; sheet: string | null; row: number | null; source: string }
  fields: TraceField[]; history: TraceStep[]; overrides: OverrideRule[]; problems: string[]
  focus: (TraceField & { edits: { version: number; kind: string; by: string; at: string; note: string | null; before: unknown; after: unknown }[]; overrides: OverrideRule[]; previous: ({ round: { id: string; label: string | null; dataset_id: string } } & Partial<TraceField>) | null; cause: Cause }) | null
}
export type Answer = { intent: string; ai: boolean; answer: string; evidence: unknown[]; suggestions: string[]; trace?: { identity: string; pointer: string | null } }

// ---------------------------------------------------------------------------------------------
export type Preferences = { display_name: string; avatar?: string; language: 'th' | 'en'; theme: 'light' | 'dark' | 'system'; timezone: string; date_format: 'day-first' | 'iso'; memory_enabled: boolean; chat_search: boolean; memory_sensitive: boolean }
export type MemoryItem = { id: string; topic: string; content: string; origin: 'chat' | 'user'; sensitive: boolean; source_chat_id: string | null; created_at: string; updated_at: string }
export type MemoryChange = { action: 'save' | 'change' | 'forget'; id: string; topic: string; sensitive?: boolean }
export type HistoryFilters = { status?: string; customer_id?: string; search?: string; start?: string; end?: string; timezone?: string; offset?: number; limit?: number }
export type ReviewQuestions = { mapping_id: string; round_id: string; status: string; blocked: boolean; questions: { id: string; field: FieldSpec; kind: 'boolean' | 'number' | 'confirm'; current: Expr; evidence: string[]; note?: string }[]; open_questions: string[]; reasons: string[]; sample: Record<string, unknown>[]; context: { customer: { code: string; name: string } | null; round_label: string | null; files: { id: string; name: string }[]; mapping_version: number }; changes: (FieldChange & { field: FieldSpec })[]; comparison_dataset_id: string | null; checks: Record<string, unknown>; impact: PolicyDecision['impact'] | null; fields: FieldSpec[] }
export type ReviewEvidence = { field: string; sample_limit: number; sample_records: number; constant: boolean; context_only: boolean; unavailable: boolean; examples: { identity: string; item: number | null; value: unknown; source_complete: boolean; cells: { file: string | null; sheet: string | null; row: number | null; column: string | null; header: string; raw: unknown; verified: boolean | null }[] }[] }
export type AnswerPreview = { digest: string; remaining_questions: string[]; before: Record<string, unknown>[]; comparison: { counts: { changed_records: number; lost: number; gained: number }; rows: SandboxItem[]; sample_limit: number; before_records: number; after_records: number; reliable: boolean; confirmed_fields: string[] }; preview: { ok: boolean; records: Record<string, unknown>[]; findings: Finding[]; validation?: { ok: boolean }; stats?: Record<string, unknown>; errors?: string[] } }
export type AssistantAction = { id: string; label: string; detail: string; url: string; kind?: 'act'; act?: { type: 'verdict.accept'; round_id: string; digest: string; decisions?: Record<string, boolean | number | string | null>; preview?: Bilingual[] } }
export type Clarification = { question: string; reason: string; options: string[] }
export type AssistantReport = {
  schema_version: number; id: string; evidence_id: number; title: { en: string; th: string };
  scope: { en: string; th: string }; generated_at: string; complete: boolean;
  columns: { key: string; label: { en: string; th: string }; type: 'number' | 'text' | 'date' | 'status' }[];
  rows: Record<string, string | number | null>[]; series: string[];
  views: ('table' | 'bar' | 'line')[]; view: 'table' | 'bar' | 'line';
  notes: { en: string; th: string }[]; links: { label: string; url: string }[];
}
export type ChatFile = { id: string; name: string; media_type: string; size: number; kind: 'upload' | 'output' | 'preview' }
export type ArtifactKind = 'flow' | 'deck' | 'doc' | 'sheet' | 'web' | 'image' | 'text' | 'file'
export type ArtifactRef = { id: string; kind: ArtifactKind; title: string; version: number; note: string }
export type FlowNode = { id: string; label: string; detail?: string; tone?: 'plain' | 'question' | 'problem' | 'done' }
export type FlowEdge = { from: string; to: string; label?: string; back?: boolean }
export type FlowContent = { nodes: FlowNode[]; edges: FlowEdge[]; direction: 'down' | 'right' }
export type SheetPreview = { name: string; rows: string[][]; formulas: Record<string, string>; total_rows: number; total_columns: number }
export type ArtifactVersion = { number: number; note: string; restored_from: number | null; file: ChatFile | null; content: FlowContent | null; preview: { status: 'ready' | 'failed' | 'none'; pages?: string[]; pdf?: string | null; sheets?: SheetPreview[] } | null; code: string | null; checked: number; generated_at: string | null; uses_data: boolean; created_at: string }
export type ArtifactSummary = { id: string; conversation_id: string; kind: ArtifactKind; title: string; name: string; latest: number; created_at: string; updated_at: string; chat_title?: string }
export type ArtifactDetail = ArtifactSummary & { stale: boolean; versions: ArtifactVersion[] }
export type CodeRun = { id: string; code: string; status: 'ok' | 'error' | 'timeout' | 'killed'; exit_code: number; duration_ms: number; stdout: string; stderr: string; files: ChatFile[]; omitted: string[]; inputs: string[] }
export type WorkspaceAnswer = { answer: string; ai: boolean; reason?: string; clarifications?: Clarification[]; tools_used?: string[]; actions?: AssistantAction[]; visualizations?: AssistantReport[]; code_runs?: CodeRun[]; artifacts?: ArtifactRef[]; memory_changes?: MemoryChange[]; evidence: { id: number; tool: string; data: unknown; links: { label: string; url: string }[] }[] }

export type ChatSummary = { id: string; title: string; round_id: string | null; dataset_id: string | null; mapping_id?: string | null; archived: boolean; pinned: boolean; memory_off?: boolean; incognito?: boolean; created_at: string; updated_at: string; busy: boolean }
export type ChatTurn = { id: string; request_id: string; sequence: number; question: string; reply: WorkspaceAnswer | null; attachments?: ChatFile[]; status: 'pending' | 'complete' | 'failed' | 'stopped'; created_at: string }
export type ChatDetail = { conversation: ChatSummary; turns: ChatTurn[]; total: number; older_before: number | null }
export type ChatList = { items: ChatSummary[]; total: number; next_offset: number | null }

export const api = {
  chats: (search = '', offset = 0, archived = false) => get<ChatList>(`/api/assistant/conversations?search=${encodeURIComponent(search)}&offset=${offset}&archived=${archived}`),
  createChat: (b: { round_id?: string; dataset_id?: string; mapping_id?: string; memory_off?: boolean; incognito?: boolean }) => post<ChatSummary>('/api/assistant/conversations', b),
  importChat: (b: { round_id?: string; dataset_id?: string; messages: { question: string; answer: string }[] }) => post<ChatSummary>('/api/assistant/conversations/import', b),
  chat: (id: string, before?: number) => get<ChatDetail>(`/api/assistant/conversations/${encodeURIComponent(id)}${before ? '?before=' + before : ''}`),
  editChat: (id: string, b: { title?: string; archived?: boolean; pinned?: boolean }) => patch<ChatSummary>(`/api/assistant/conversations/${encodeURIComponent(id)}`, b),
  deleteChat: (id: string) => del<void>(`/api/assistant/conversations/${encodeURIComponent(id)}`),
  chatSend: (id: string, b: { question: string; request_id: string; language: 'th' | 'en'; timezone: string }) => post<ChatTurn>(`/api/assistant/conversations/${encodeURIComponent(id)}/messages`, b),
  chatStream,
  chatFile,
  attachChatFile,
  memory: () => get<{ items: MemoryItem[] }>('/api/assistant/memory'),
  addMemory: (b: { topic: string; content: string }) => post<MemoryItem>('/api/assistant/memory', b),
  editMemory: (id: string, b: { topic?: string; content?: string }) => patch<MemoryItem>(`/api/assistant/memory/${encodeURIComponent(id)}`, b),
  deleteMemory: (id: string) => del<void>(`/api/assistant/memory/${encodeURIComponent(id)}`),
  resetMemory: () => del<{ deleted: number }>('/api/assistant/memory'),
  artifacts: () => get<{ items: ArtifactSummary[] }>('/api/assistant/artifacts'),
  artifact: (id: string) => get<ArtifactDetail>(`/api/assistant/artifacts/${encodeURIComponent(id)}`),
  restoreArtifact: (id: string, number: number) => post<ArtifactDetail>(`/api/assistant/artifacts/${encodeURIComponent(id)}/versions/${number}/restore`),
  chatStop: (id: string, request_id: string) => post<ChatTurn>(`/api/assistant/conversations/${encodeURIComponent(id)}/stop`, { request_id }),
  preferences: () => request<Preferences>('/api/me/preferences', { cache: 'no-store' }),
  savePreferences: (b: Preferences) => put<Preferences>('/api/me/preferences', b),
  history: (q: HistoryFilters) => get<{ items: ImportRound[]; total: number; offset: number; limit: number; timezone: string }>('/api/history' + qs(q)),
  foresight: () => get<Foresight>('/api/foresight'),
  calibration: () => get<{ report: CalibrationReport; proposals: Proposal[] }>('/api/calibration'),
  applyProposal: (b: { id: string; digest: string }) => post<{ applied: string; path: string; value: unknown; scope: string }>('/api/calibration/apply', b),
  learning: (customer_id = '', offset = 0) => get<LearningView>('/api/learning' + qs({ customer_id: customer_id || undefined, offset })),
  promoteAlias: (b: { header: string; target: string }) => post<{ id: string }>('/api/learning/promote', b),
  forgetLearned: (id: string) => del<void>(`/api/learning/${encodeURIComponent(id)}`),
  verdict: (roundId: string) => get<{ round_id: string; status: string; verdict: Verdict | null }>(`/api/rounds/${roundId}/verdict`),
  acceptVerdict: (roundId: string, b: { digest: string; decisions?: Record<string, boolean | number | string | null>; comment?: string }) => post<VerdictAccepted>(`/api/rounds/${roundId}/verdict/accept`, b),
  reviewQuestions: (id: string) => get<ReviewQuestions>(`/api/mappings/${id}/questions`),
  reviewEvidence: (id: string, field: string) => get<ReviewEvidence>(`/api/mappings/${id}/questions/evidence?field=${encodeURIComponent(field)}`),
  previewAnswers: (id: string, answers: Record<string, boolean | number | string>) => post<AnswerPreview>(`/api/mappings/${id}/answers/preview`, { answers }),
  applyAnswers: (id: string, answers: Record<string, boolean | number | string>, digest: string) => post<MappingVersion>(`/api/mappings/${id}/answers/apply`, { answers, digest }),
  assistantStatus: () => get<{ enabled: boolean; provider: string; model: string; tools: string[]; vision: boolean; code_sandbox: boolean; file_types: string[] }>('/api/assistant/status'),
  workspaceAsk: (b: { question: string; language: 'th' | 'en'; timezone: string; round_id?: string; dataset_id?: string; conversation: { role: 'user' | 'assistant'; content: string }[] }) => post<WorkspaceAnswer>('/api/assistant/ask', b),
  dashboard: () => get<Dashboard>('/api/dashboard'),
  customers: () => get<Customer[]>('/api/customers'),
  customer: (id: string) => get<Customer>(`/api/customers/${id}`),
  createCustomer: (b: { code: string; name: string; erp_type?: string; notes?: string }) => post<Customer>('/api/customers', b),
  updateCustomer: (id: string, b: Partial<Pick<Customer, 'code' | 'name' | 'erp_type' | 'notes' | 'active' | 'default_target_schema'>>) => patch<Customer>(`/api/customers/${id}`, b),
  deleteOverride: (cid: string, oid: string) => del<{ ok: boolean }>(`/api/customers/${cid}/overrides/${oid}`),

  rounds: (q?: { status?: string; customer_id?: string }) => get<ImportRound[]>('/api/rounds' + qs(q)),
  round: (id: string) => get<ImportRound>(`/api/rounds/${id}`),
  createRound: (b: { customer_id: string; label?: string; target_schema?: string }) => post<ImportRound>('/api/rounds', b),
  uploadFiles: (roundId: string, files: File[]) => {
    const fd = new FormData()
    files.forEach((f) => fd.append('files', f))
    return request<SourceFile[]>(`/api/rounds/${roundId}/files`, { method: 'POST', body: fd })
  },
  createUpload: (files: File[], o: { label?: string; work_date?: string; expected_files?: number; auto_publish: boolean }) => {
    const fd = new FormData()
    files.forEach((f) => fd.append('files', f))
    if (o.label) fd.append('label', o.label)
    if (o.work_date) fd.append('work_date', o.work_date)
    if (o.expected_files !== undefined) fd.append('expected_files', String(o.expected_files))
    fd.append('auto_publish', String(o.auto_publish))
    return request<Batch>('/api/batches', { method: 'POST', body: fd })
  },
  uploads: (q?: { limit?: number; offset?: number; search?: string; start?: string; end?: string }) => get<BatchSummary[]>('/api/batches' + qs(q)),
  appendUpload: (id: string, files: File[]) => {
    const fd = new FormData()
    files.forEach(f => fd.append('files', f))
    return request<Batch & { added: number; skipped: number }>(`/api/batches/${id}/files`, { method: 'POST', body: fd })
  },
  upload: (id: string) => get<Batch>(`/api/batches/${id}`),
  editUpload: (id: string, body: { label?: string; work_date?: string; expected_files?: number | null }) => patch<Batch>(`/api/batches/${id}`, body),
  assignUpload: (id: string, b: { item_ids: string[]; customer_id?: string; new_customer?: { code: string; name: string; erp_type?: string } }) => post<Batch>(`/api/batches/${id}/assign`, b),
  resumeUpload: (id: string) => post<Batch>(`/api/batches/${id}/resume`, {}),
  uploadDownloadUrl: (id: string) => `/api/batches/${id}/download`,
  file: (roundId: string, fileId: string) => get<SourceFile>(`/api/rounds/${roundId}/files/${fileId}`),
  fileGrid: (roundId: string, fileId: string, sheet = 0, rows = 60) => get<{ sheet: number; n_cols: number; rows: (string | null)[][] }>(`/api/rounds/${roundId}/files/${fileId}/grid?sheet=${sheet}&rows=${rows}`),
  runPipeline: (roundId: string, b?: { use_llm?: boolean | null; actor?: string }) => post<{ job_id: string }>(`/api/rounds/${roundId}/run`, b ?? {}),
  propose: (roundId: string, b?: { use_llm?: boolean | null; actor?: string }) => post<{ job_id: string }>(`/api/rounds/${roundId}/propose`, b ?? {}),
  profile: (roundId: string) => post<{ job_id: string }>(`/api/rounds/${roundId}/profile`),
  transform: (roundId: string, mappingId?: string) => post<{ job_id: string }>(`/api/rounds/${roundId}/transform${mappingId ? `?mapping_version_id=${mappingId}` : ''}`),
  job: (id: string) => get<Job>(`/api/jobs/${id}`),
  jobs: (q?: { status?: string; kind?: string; limit?: number }) => get<Job[]>('/api/jobs' + qs(q)),
  jobStats: () => get<QueueStats>('/api/jobs/stats'),
  batch: (id: string) => get<BatchStatus>(`/api/jobs/batch/${id}`),
  batchRun: (round_ids: string[], b?: { use_llm?: boolean | null; force?: boolean }) => post<{ batch_id: string; job_ids: string[]; count: number }>('/api/rounds/batch-run', { round_ids, ...(b ?? {}) }),
  retryJob: (id: string) => post<{ job_id: string }>(`/api/jobs/${id}/retry`),
  me: () => get<Me>('/api/me'),
  ready: () => get<Ready>('/ready'),
  ops: () => get<OpsSummary>('/api/ops'),

  mapping: (id: string) => get<MappingVersion>(`/api/mappings/${id}`),
  approveMapping: (id: string, b: { actor: string; comment?: string; override_block?: boolean }) => post<MappingVersion>(`/api/mappings/${id}/approve`, b),
  rejectMapping: (id: string, b: { actor: string; comment?: string }) => post<MappingVersion>(`/api/mappings/${id}/reject`, b),
  editMapping: (id: string, document: MappingDocument, actor = 'operator') => post<MappingVersion>(`/api/mappings/${id}/edit`, { document, actor }),
  mappingPreview: (roundId: string, document: MappingDocument, limit = 50) => post<{ ok: boolean; records: Record<string, unknown>[]; findings: Finding[]; validation?: { ok: boolean; issues: { level: string; code: string; message: string; path: string }[] }; errors?: string[]; stats?: Record<string, unknown> }>(`/api/rounds/${roundId}/mapping-preview`, { document, limit }),
  sourcePreview: (roundId: string, source: SourceSpec, limit = 60) => post<{ file: string; columns: string[]; rows: unknown[][]; n_rows_preview: number }>(`/api/rounds/${roundId}/source-preview`, { source, limit }),
  diagnose: (mappingId: string) => post<{ ai: boolean; findings: Finding[]; root_causes: { finding: string; cause: string; fix: string; fix_kind?: string; severity?: string; evidence?: string[] }[]; summary_th?: string; patched_mapping?: MappingDocument | null }>(`/api/mappings/${mappingId}/diagnose`),
  relationships: (roundId: string, document?: MappingDocument, probe?: { from: RelEnd; to: RelEnd }) => post<Relationships>(`/api/rounds/${roundId}/relationships`, { ...(document ? { document } : {}), ...(probe ? { probe } : {}) }),
  compareMapping: (id: string, document?: MappingDocument) => post<SandboxReport>(`/api/mappings/${id}/compare`, document ? { document } : {}),
  sandboxList: (id: string, list: string, offset = 0, limit = 100) => get<{ report: SandboxReport | null; list: string; total: number; items: SandboxItem[] }>(`/api/mappings/${id}/sandbox?list=${list}&offset=${offset}&limit=${limit}`),
  dsl: () => get<{ ops: string[]; json_schema: unknown }>('/api/dsl'),

  review: (status = 'open', round_id = '', offset = 0) => get<ReviewTask[]>('/api/review' + qs({ status, round_id: round_id || undefined, offset, limit: 50 })),

  dataset: (id: string) => get<DatasetVersion>(`/api/datasets/${id}`),
  rows: (id: string, q: { offset?: number; limit?: number; q?: string; sort?: string; desc?: boolean }) => get<RowsPage>(`/api/datasets/${id}/rows` + qs(q)),
  /** validation_errors = errors this change would add (they block the save); invalid_before/after = records failing the format */
  previewChanges: (id: string, operations: Operation[]) => post<{ impact: Impact; validation_errors: { row: number; identity: string; pointer: string; message: string }[]; invalid_before: number; invalid_after: number; rows_after: number }>(`/api/datasets/${id}/preview-changes`, { operations }),
  applyChanges: (id: string, b: { operations: Operation[]; kind: string; actor: string; note?: string }) => post<{ dataset: DatasetVersion; change_set: ChangeSet }>(`/api/datasets/${id}/changes`, b),
  rollback: (id: string, actor = 'operator') => post<DatasetVersion>(`/api/datasets/${id}/rollback`, { actor }),
  approveDataset: (id: string, b: { acknowledge_anomalies?: boolean; comment?: string } = {}) => post<DatasetVersion>(`/api/datasets/${id}/approve`, { actor: 'operator', ...b }),
  trace: (id: string, identity: string, pointer?: string) => get<Trace>(`/api/datasets/${id}/trace/${encodeURIComponent(identity)}` + qs(pointer ? { pointer } : undefined)),
  ask: (id: string, question: string) => post<Answer>(`/api/datasets/${id}/ask`, { question }),
  fieldReview: (mappingId: string) => get<FieldReview>(`/api/mappings/${mappingId}/field-review`),
  changeReport: (id: string) => get<ChangeReport>(`/api/datasets/${id}/changes-report`),
  lineage: (id: string, identity: string) => get<{ record: Record<string, unknown>; source_meta: Record<string, unknown>; mapping_version: string; fields: FieldMapping[]; collections: CollectionMapping[]; edits: { change_set: string; kind: string; author: string; at: string; changes: Impact['sample'] }[] }>(`/api/datasets/${id}/lineage/${encodeURIComponent(identity)}`),
  exportDataset: (id: string, fmt: string) => post<ExportArtifact>(`/api/datasets/${id}/export?fmt=${fmt}`),
  copilot: (id: string, instruction: string) => post<{ ai: boolean; operations: Operation[]; impact?: Impact; needs_clarification: string | null }>(`/api/datasets/${id}/copilot`, { instruction }),
  downloadUrl: (exportId: string) => `/api/exports/${exportId}/download`,

  schemas: () => get<SchemaSummary[]>('/api/schemas'),
  schema: (ref: string) => get<SchemaSummary>(`/api/schemas/${ref}`),
  policy: () => get<Record<string, unknown>>('/api/policy'),
  previewSchema: (document: unknown) => post<SchemaPreview>('/api/schemas/preview', { document }),
  registerSchema: (document: unknown) => post<SchemaSummary>('/api/schemas', { document }),
  semantics: () => get<Record<string, string>>('/api/settings/semantics'),
  setDefaultFormat: (target_schema: string) => put<{ target_schema: string }>('/api/settings/default-format', { target_schema }),
  aiSettings: () => get<AISettings>('/api/settings/ai'),
  testVision: () => post<{ results: { id: string; model: string; vision: boolean | null; message: string }[]; message?: string; ai: AISettings }>('/api/settings/ai/vision-test'),
  saveAiSettings: (b: AISettingsPatch) => put<AISettings>('/api/settings/ai', b),
  connectAi: (api_key: string, provider?: string, model?: string, base_url?: string) => post<{ test: { ok: boolean; message: string; model?: string; provider?: string; latency_ms?: number }; ai: AISettings }>('/api/settings/ai/connect', { api_key, provider, model, base_url }),
  discoverModels: (b: { provider: string; api_key?: string; base_url?: string }) => post<{ models: string[]; source: string; message: string; truncated: boolean }>('/api/settings/ai/models', b),
  testAi: () => post<AiProbe & { route?: string; backups?: (AiProbe & { id: string })[] }>('/api/settings/ai/test'),
  addBackupKey: (b: { api_key: string; provider?: string; model?: string; base_url?: string }) => post<{ test: AiProbe & { id: string }; ai: AISettings }>('/api/settings/ai/backups', b),
  updateBackupKey: (id: string, b: { model?: string; base_url?: string | null }) => patch<AISettings>(`/api/settings/ai/backups/${id}`, b),
  removeBackupKey: (id: string) => del<AISettings>(`/api/settings/ai/backups/${id}`),
  orderBackupKeys: (ids: string[]) => put<AISettings>('/api/settings/ai/backups/order', { ids }),
  policySettings: () => get<PolicyView>('/api/settings/policy'),
  savePolicy: (policy: Policy) => put<PolicyView>('/api/settings/policy', { policy }),
  resetPolicy: () => del<PolicyView>('/api/settings/policy'),
  audit: (limit = 100) => get<{ id: string; actor: string; action: string; entity: string; entity_id: string; details: Record<string, unknown>; at: string }[]>(`/api/audit?limit=${limit}`),
}

function qs(q?: Record<string, unknown>): string {
  if (!q) return ''
  const p = Object.entries(q).filter(([, v]) => v !== undefined && v !== null && v !== '').map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
  return p.length ? `?${p.join('&')}` : ''
}

/** Poll a job until it finishes. */
export async function waitForJob(jobId: string, onTick?: (j: Job) => void, intervalMs = 700): Promise<Job> {
  for (;;) {
    const j = await api.job(jobId)
    onTick?.(j)
    if (j.status === 'done' || j.status === 'failed') return j
    await new Promise((r) => setTimeout(r, intervalMs))
  }
}
