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

// ---------------------------------------------------------------------------------------------
// Types (mirror backend serializers)
// ---------------------------------------------------------------------------------------------
export type Customer = {
  id: string; code: string; name: string; erp_type: string | null; notes: string | null; active: boolean; created_at: string
  rounds?: number | ImportRound[]; last_round_at?: string | null; has_approved_mapping?: boolean
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
  id: string; customer_id: string; label: string | null; status: RoundStatus; target_schema: string; notes: string | null
  summary: { drift?: string | null; drift_report?: DriftReport | null; decision?: string; mapping_version_id?: string; dataset_version_id?: string; rows?: number; errors?: number; error?: string; simulation?: SimSummary | null; has_previous_approval?: boolean }
  created_at: string; updated_at: string; files?: SourceFile[]; customer?: { id: string; code: string; name: string }
  mappings?: MappingVersion[]; datasets?: DatasetVersion[]; review_tasks?: ReviewTask[]; jobs?: Job[]
}
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
export type PolicyDecision = { decision: 'auto_approve' | 'review' | 'block'; priority: number; reasons: string[]; risk_score: number; checks: Record<string, unknown>; source?: string; simulation?: SimSummary | null }
export type MappingVersion = {
  id: string; customer_id: string; round_id: string | null; version: number; status: 'proposed' | 'approved' | 'rejected' | 'superseded'; target_schema: string
  confidence: number | null; created_by: string; model: string | null; policy: PolicyDecision; open_questions: string[]; validation: { ok: boolean; issues: { level: string; code: string; message: string; path: string }[] } | null
  parent_id: string | null; approved_by: string | null; approved_at: string | null; comment: string | null; created_at: string; agent_log: Record<string, unknown>[]; sample: Record<string, unknown>[]; document?: MappingDocument
}
export type ReviewTask = {
  id: string; round_id: string; customer_id: string; mapping_version_id: string | null; dataset_version_id: string | null; kind: string; priority: number; status: string
  reasons: string[]; details: { decision?: string; open_questions?: string[]; findings?: Finding[] }; resolver: string | null; comment: string | null; created_at: string; resolved_at: string | null
  customer?: { id: string; code: string; name: string } | null; round?: { id: string; label: string | null; status: string } | null
}
export type DatasetVersion = {
  id: string; round_id: string; customer_id: string; mapping_version_id: string; version: number; status: string; row_count: number
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
  breaker: { open: boolean; failures: number; opened_at?: string | null } | null
  storage: string; database: string; auth: string; tracing: boolean; schema_revision: string | null
}
export type FieldSpec = { pointer: string; name: string; json_type: string[]; semantic: string | null; risk: 'critical' | 'high' | 'medium' | 'low'; required: boolean; nullable: boolean; enum: unknown[] | null; format: string | null; synonyms_en: string[]; synonyms_th: string[]; description: string | null; description_th: string | null; in_collection: string | null; is_collection: boolean; system: boolean; default: unknown }
export type SchemaSummary = { schema_id: string; version: number; ref: string; entity: string; status: string; title: string; description: string; identity_keys: string[]; field_count: number; fields?: FieldSpec[]; document?: unknown }
export type Operation = { kind: 'set_cell' | 'set_field' | 'scale_field' | 'delete_rows' | 'delete_row' | 'set_item'; target?: string; identity?: string; value?: unknown; factor?: number; where?: Expr | null; explanation?: string }
export type Impact = { rows_affected: number; cells_changed: number; rows_deleted: number; fields: string[]; sample: { row: number; identity: string; target: string | null; before: unknown; after: unknown }[] }
export type RowsPage = { total: number; offset: number; limit: number; identity_keys: string[]; rows: Record<string, unknown>[]; columns: FieldSpec[]; findings: Finding[] }
export type Dashboard = {
  customers: number; rounds_by_status: Record<string, number>; open_reviews: number; approved_mappings: number; auto_approved_mappings: number; mapping_versions: number; reused_mappings: number
  published_rows: number; failed_jobs: number; llm: { calls: number; cost_usd: number; tokens: number; provider: string; model: string }; recent_rounds: ImportRound[]; automation_rate: number | null
}
export type ChangeReport = {
  has_previous: boolean; current_rows: number; previous_rows?: number; previous_dataset_id?: string; added?: string[]; removed?: string[]; added_count?: number; removed_count?: number; changed_count?: number
  changed?: { identity: string; changes: { pointer: string; before: unknown; after: unknown }[] }[]; price_changes?: { identity: string; before: number; after: number; pct: number | null }[]; price_change_count?: number; large_price_moves?: { identity: string; before: number; after: number; pct: number | null }[]; promotion_changes?: number
}

// ---------------------------------------------------------------------------------------------
export const api = {
  dashboard: () => get<Dashboard>('/api/dashboard'),
  customers: () => get<Customer[]>('/api/customers'),
  customer: (id: string) => get<Customer>(`/api/customers/${id}`),
  createCustomer: (b: { code: string; name: string; erp_type?: string; notes?: string }) => post<Customer>('/api/customers', b),
  deleteOverride: (cid: string, oid: string) => del<{ ok: boolean }>(`/api/customers/${cid}/overrides/${oid}`),

  rounds: (q?: { status?: string; customer_id?: string }) => get<ImportRound[]>('/api/rounds' + qs(q)),
  round: (id: string) => get<ImportRound>(`/api/rounds/${id}`),
  createRound: (b: { customer_id: string; label?: string; target_schema?: string }) => post<ImportRound>('/api/rounds', b),
  uploadFiles: (roundId: string, files: File[]) => {
    const fd = new FormData()
    files.forEach((f) => fd.append('files', f))
    return request<SourceFile[]>(`/api/rounds/${roundId}/files`, { method: 'POST', body: fd })
  },
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
  dsl: () => get<{ ops: string[]; json_schema: unknown }>('/api/dsl'),

  review: (status = 'open') => get<ReviewTask[]>(`/api/review?status=${status}`),

  dataset: (id: string) => get<DatasetVersion>(`/api/datasets/${id}`),
  rows: (id: string, q: { offset?: number; limit?: number; q?: string; sort?: string; desc?: boolean }) => get<RowsPage>(`/api/datasets/${id}/rows` + qs(q)),
  previewChanges: (id: string, operations: Operation[]) => post<{ impact: Impact; validation_errors: { row: number; pointer: string; message: string }[]; rows_after: number }>(`/api/datasets/${id}/preview-changes`, { operations }),
  applyChanges: (id: string, b: { operations: Operation[]; kind: string; actor: string; note?: string }) => post<{ dataset: DatasetVersion; change_set: ChangeSet }>(`/api/datasets/${id}/changes`, b),
  rollback: (id: string, actor = 'operator') => post<DatasetVersion>(`/api/datasets/${id}/rollback`, { actor }),
  approveDataset: (id: string, actor = 'operator') => post<DatasetVersion>(`/api/datasets/${id}/approve`, { actor }),
  changeReport: (id: string) => get<ChangeReport>(`/api/datasets/${id}/changes-report`),
  lineage: (id: string, identity: string) => get<{ record: Record<string, unknown>; source_meta: Record<string, unknown>; mapping_version: string; fields: FieldMapping[]; collections: CollectionMapping[]; edits: { change_set: string; kind: string; author: string; at: string; changes: Impact['sample'] }[] }>(`/api/datasets/${id}/lineage/${encodeURIComponent(identity)}`),
  exportDataset: (id: string, fmt: string) => post<ExportArtifact>(`/api/datasets/${id}/export?fmt=${fmt}`),
  copilot: (id: string, instruction: string) => post<{ ai: boolean; operations: Operation[]; impact?: Impact; needs_clarification: string | null }>(`/api/datasets/${id}/copilot`, { instruction }),
  downloadUrl: (exportId: string) => `/api/exports/${exportId}/download`,

  schemas: () => get<SchemaSummary[]>('/api/schemas'),
  schema: (ref: string) => get<SchemaSummary>(`/api/schemas/${ref}`),
  policy: () => get<Record<string, unknown>>('/api/policy'),
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
