# ADR-042: Explain review evidence and ask before assuming

Review queue reasons are translated deterministically into bilingual explanations. Counts retain their
original matched-record scope, schema metadata/semantics supply field labels, and raw diagnostics remain
available under a technical disclosure. Unknown diagnostics have a conservative fallback.

The review assistant button creates a durable conversation with the exact mapping ID and its import round,
then sends a file-explanation question (including the selected review question when applicable). Creation
validates customer access and mapping/round consistency. `inspect_review` rechecks access on every call,
returns current state of that immutable version, flags whether it is latest, and reads bounded actual source
evidence through the same review evidence service. The context is a starting point, not a restriction on
other FORMA questions. Migration f3b4c5d6e7f8 adds a nullable mapping reference for existing chats.
Change summaries are paginated and omit full rule documents so source evidence fits in context; full rules
remain available through `inspect_mapping`. Tool evidence is normalized to JSON before both model use and
chat persistence, including dates and decimal values, preventing completed replies from failing to save.

The assistant's validated response can include up to three clarification questions with optional choices.
It must retrieve available evidence first, ask for material missing information, and leave unknown/skipped
answers unresolved. In the UI, no choice is preselected; users can select, type, go back, skip, or ask a new
chat question. Submitted answers are ordinary durable conversation turns. Clarification cards are persisted
in reply JSON and included in recent and retrieved conversation history. Only the latest completed turn's
card accepts responses. Chat answers never save mapping rules or approve business data.

Clarification replies use the validated short answer directly over SSE, without a second prose-generation
pass that could contradict the questions. Mutation actions are removed from these replies; evidence and
navigation remain available. Ordinary answers retain provider text streaming and Stop behavior.

Validation: focused backend tests cover exact/scoped context, source evidence, durable clarification cards,
stream delivery, follow-up history, and deterministic explanation counts. Existing workspace, conversation,
stream and review-evidence tests, strict mypy, ruff, frontend build and oxlint also run. Live provider behavior
still depends on the configured model; the system does not claim infallible uncertainty detection.
