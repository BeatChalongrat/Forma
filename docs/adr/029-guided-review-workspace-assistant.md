# ADR-029: Guided review, personal preferences and workspace tools

Status: accepted, 2026-10-29. Extends ADR-023, ADR-025, ADR-027 and ADR-028.

## User experience

The operator sees three stages: upload, answer/check, download. Profiling, mapping, simulation,
transformation and data approval remain separate backend stages and policy gates. Advanced mapping,
lineage, comparisons and the data editor remain available through disclosures and contextual links.
The review queue prioritizes a short description of the problem; raw policy reasons are expandable.

Question answers are deterministic, schema-driven edits. Boolean/numeric constants can be supplied;
other uncertain fields can be explicitly confirmed or opened in Mapping Studio. Field meaning and
type come from the target schema, never hard-coded target paths. Answers are previewed, then saved
as a new mapping with parent ID, actor and evidence. The mapping is simulated and policy evaluated
again. The original source and parent document remain immutable. Unknown questions stay unresolved;
an answer does not clear wrong-file drift blocks or approve a result automatically.

## History and preferences

`GET /api/history` filters and paginates in the database, before returning results. Dates refer to the
original bulk file receipt time, or the first source-file upload time for a manual round. Empty rounds
are excluded. Dates are inclusive local calendar days in the selected IANA time zone, converted to
UTC as a half-open interval. Filename, delivery/batch label, customer and status can be combined.
Filters live in the URL and survive browser Back. Recent unassigned upload batches remain separately
accessible and are explicitly labelled as not using the round filters.

Personal display name, Thai/English, light/dark/system, time zone and date format are stored under a
hash of `(auth mode, subject)` in AppSetting through the audited configuration service. No new table
or migration is needed. Preferences do not change authentication claims or audit identity. Role names,
source column names, DSL and original diagnostic evidence remain verbatim. The principal UI paths
and assistant answers use the chosen language. Advanced diagnostic strings may remain in their
original language. OIDC users can sign out; dev users see that they are using a local demo account.

## Workspace assistant and MCP

The assistant uses a bounded structured-output loop (six model turns, at most two tool calls per
turn). Its tools share one dispatcher enforcing Principal roles and customer scope. Tools retrieve
workspace counts, feature documentation, paginated history, an import's questions and source samples,
dataset records, record search, measured lineage/issue explanations and target contracts. Model
responses pass the existing output guard and must cite actual evidence IDs. Samples, pagination and
truncated evidence are explicitly marked. Tools cannot mutate or approve business data. Provider
errors and missing credentials produce an explicit non-AI fallback, never a fabricated AI answer.
Successful model calls are recorded in the LLM usage ledger. Per-user AI request limits apply.

The assistant now lives at `/assistant` as a full conversation workspace. Contextual entry points
carry an import or dataset ID. Up to twelve exchanges are retained in this browser tab's session
storage, partitioned by subject, authentication mode, roles, customer scope and selected data context.
The last four exchanges provide conversational context; current facts must still be retrieved again.
New conversation clears that tab's context. There is no server-side conversation history or streaming
claim: the UI shows a pending lookup until a validated complete response arrives.

The composer is the only scope indicator: "Search all FORMA", or the current file context with a clear
context button. Answers can include up to three next-step navigation buttons. The server builds their
destinations from scoped tool results and known app routes; the model selects opaque action IDs from
cited evidence, never supplies executable actions or arbitrary URLs. Review links carry the round and
status; history links preserve search/date/customer/status/time-zone filters. Clicking only navigates.
Source links remain available inside the evidence disclosure. Older cached answers remain readable;
new answers receive the navigation choices.

Nineteen read-only tools cover customers, mapping versions and persistent overrides, source cells,
dataset records and exact aggregates, review tasks, dataset changes/exports, bulk uploads, processing
jobs, safe runtime configuration, audit metadata and feature guidance. Source lookup searches the
entire selected sheet and returns explicit row/column pages. Numeric aggregation streams every record
in one immutable dataset version using Decimal and schema-approved pointers; collection values,
missing/non-numeric values and scan scope are reported explicitly. It never adds historical versions
together or estimates totals from a preview. The legacy text-search tool retains its explicit 100,000
record scan ceiling; exact/full scans use `query_records` instead.

Tools expose relevant business data on demand, not an unbounded copy of the database in every prompt.
Each evidence item is limited to 18,000 characters with an explicit truncation marker. Very large
documents may require narrower queries. Source cells are reader-normalized values, not recalculated
Excel formulas. System credentials, raw worker errors, storage paths and personal profile payloads
are excluded. Bulk unassigned files require unrestricted customer access; audit metadata requires an
unrestricted administrator. Current approval policy is administrator-only. The existing FORMA UI
remains the place for edits, approvals, downloads and full operational diagnostics.

The same tools are exposed at `POST /api/mcp`, a stateless Streamable HTTP JSON-RPC endpoint with
initialize, ping, tools/list and tools/call. GET returns 405 because no unsolicited event stream is
provided. Notifications return 202. Origins are checked against the configured allowlist and every
request uses existing authentication/RBAC. It supports protocol versions 2025-03-26, 2025-06-18 and
2025-11-25. There is no separate OAuth authorization server or automatic connection to an external
assistant: clients must supply the existing FORMA Bearer token in OIDC mode. Local dev mode has the
same intentionally permissive demo identity as the rest of the app and must remain local.

References: [MCP transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports),
[MCP tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools).

## Provider endpoint and model discovery

The main AI connection form shows provider, base endpoint, key and model together. Provider selection
is explicit, so a gateway key starting with `sk-` is not forced to OpenAI. Direct providers have default
endpoints; custom gateways require their compatible base URL. Model discovery is an admin-only,
rate-limited, read-only request to the selected provider's model catalogue. It does not save the key.
Anthropic pagination is bounded; redirects are not followed; responses and timeouts are capped.
Errors do not echo provider response bodies. A saved key is reused only for the same provider and
normalized endpoint. Changing destination requires entering the intended key again.

The list describes what the endpoint advertises, not a guarantee that every model supports text chat
and the structured JSON required by FORMA. A model ID can be typed when listing is unavailable.
Test & save uses that exact provider, endpoint and model; persistence occurs only after the probe
succeeds. Existing backup routing remains supported.

Once configured, the primary connection collapses into a provider/model/endpoint summary with Edit
connection and Test connection actions. Editing reopens the existing validated form; Cancel preserves
the saved configuration. A configured badge is not a live health claim. DeepSeek's hostname receives
its own display label while retaining the OpenAI-compatible transport. The assistant uses this saved
connection directly; an additional MCP client connection is not necessary.

The profile trigger opens a non-modal dropdown anchored below the avatar. Language and theme apply
inline; photo/name and time/date use subviews within the same dropdown. Photo uploads are resized and
encoded to a small JPEG in the browser; the preferences API validates the MIME prefix, binary markers,
base64 and size. Photos are stored with the subject's preferences, not in public object storage.

References: [OpenAI models](https://developers.openai.com/api/reference/resources/models/methods/list),
[Anthropic models](https://platform.claude.com/docs/en/api/models/list).

## Verification

Regression tests cover time-zone boundaries, source rather than modification time, tenant scoping,
personal settings isolation, answer validation, preview immutability, new versions and retained block
policy, MCP lifecycle and origin/RBAC checks, explicit offline assistant behavior and bounded tool
retrieval, model listing/pagination, endpoint selection and prevention of forwarding saved keys to a
changed endpoint. Live credentials and an external MCP client are not required by these tests.
