# ADR-034: File receipt dashboard and cancellable assistant text streaming

Status: accepted; extends ADR-030 and ADR-033.

Each Batch has an optional expected_files target (1..1,000,000). Migration d84e012f931b
adds a nullable integer; no existing round is assigned an invented target. Partial PATCH
updates preserve omitted name/date/target fields and are importer-only, unrestricted-customer
operations with actor audit. Clearing the target means unknown, not zero. A lower target
never deletes files or caps further uploads.

Home offers a named work-round selector with search and older-page loading. Home and round
detail share a dashboard: received/expected, missing or over-target, and ready/received.
Processing counts (ready, needs review/customer, processing, failed) link to filtered files.
Receipt completeness and processing readiness are independent; the existing complete field
continues to describe received files only. Unknown targets are explicitly labelled. The
assistant's read-only work_rounds tool reads these figures under the existing all-customer
permission requirement and generates validated links to specific round dashboards.

The new messages/stream endpoint emits SSE started, phase, delta, reset, done and error events.
Its worker owns a separate DB session, rechecks chat ownership, and uses the existing request
id/lease protections. The question is committed before any provider call. The assistant first
selects evidence/actions through the guarded structured tool loop (a brief outline), then makes
one additional real streaming text request using only that evidence. This adds a final provider
call; no typewriter timer or fabricated streaming is used. Actions/citations appear after
completion, selected from server-generated choices, not streamed model URLs. Deltas contain
only public answer text, never reasoning_content or tool-call payloads. Incremental text passes
the output guard and is rendered as escaped React text. Guard rejection resets the provisional
answer. Partial text is provisional; only a normal provider terminator produces complete status.

OpenAI-compatible/DeepSeek and Anthropic SSE formats are supported. Normal JSON messages remain
available for old clients. Routes use their circuit breakers; a backup can be attempted before
any text is emitted, but interrupted partial answers are never mixed with another model's answer.
Reported token usage is recorded separately for the streaming call; interrupted providers may
not supply final usage, so these records cannot be treated as a complete billing ledger.

The Stop endpoint is scoped to an owned chat and exact request ID. It marks the turn stopped,
releases its lease, and closes a local active provider response. A worker on another API process
observes the DB lease on its next checkpoint. Ongoing structured evidence requests may finish
their current HTTP call, but cancellation prevents subsequent lookup/generation. Already spent
provider tokens are not refunded. A browser disconnect also cancels its active run. Partial text
is checkpointed at most every 400 ms, with final partial text saved during cleanup. A process
crash can lose the last checkpoint interval; an expired lease makes pending history retryable.
Stopped answers are labelled incomplete in recent conversational context so users can ask to continue;
they are never presented as a completed response or proof of current business facts.
Stopped request IDs are terminal; a new question uses a fresh request ID. SSE heartbeat and
X-Accel-Buffering: no keep the reverse proxy from buffering live deltas.

Validation: provider SSE packet/UTF-8 splitting, reasoning exclusion, usage, missing terminator,
upstream close, guard reset, durable endpoint replay, owned cancellation and partial persistence,
receipt-target validation/scope and migration compatibility. UI checks include real provider
streaming and stop/reopen, receipt dashboard, both themes and narrow viewports.

Live validation with the configured provider confirmed incremental streaming of one answer, that Stop
saves the partial text, that reloading preserves the stopped state and that a follow-up continues from the
incomplete answer. A five-file work round showed three files ready and two requiring review; clicking the
latter filtered the round to those two rows. Desktop light and dark themes and a 390-pixel viewport were
checked; the recent-files table's hidden header became an accessible label so an off-screen element no
longer widens the mobile document. Build, ruff, mypy and the focused backend tests pass.
