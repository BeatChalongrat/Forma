# ADR-030: Persistent assistant conversations

Status: accepted. Extends ADR-029's assistant; replaces its browser-tab-only history.

## Decision

Store conversations and ordered question/reply turns in the application database, with an Alembic
migration. Ownership derives from authentication mode and subject. An access fingerprint includes
roles and allowed customers; every list, search, read, update, message and history-tool call checks
both. A changed permission scope hides earlier chats until the matching scope returns, rather than
revealing cached customer data. Local dev auth retains the existing demo identity; it is not an
account login system. OIDC identities get isolated histories.

The sidebar exposes expandable recent chats, New chat, full-text search of questions/answers,
pagination, and an archived-chat view. Chats can be renamed, archived and restored. Selecting a chat
uses its URL. Local storage remembers only the last selected ID, scoped to the current identity;
messages are loaded from the database. History survives tab/browser and service restarts as long as
the database is retained. Database backups must include these two new tables.

Persist the question before invoking the provider. A per-conversation database lease prevents
simultaneous answers, and a unique client request ID makes retries idempotent. A run token prevents
an expired request overwriting a later retry. Disconnected clients can reopen and poll the stored
turn. Process interruption leaves an expired attempt visibly retryable after 15 minutes; generation
does not resume automatically after a process crash. Failed provider calls remain retryable without
duplicating the question. Audit events include actor and IDs, never chat contents.

The latest eight complete question/answer pairs are direct context. A read-only, owner-scoped
`conversation_history` tool retrieves older exact messages using search and pagination. Persisting
history is not unlimited model context or automatic cross-chat memory. Historical answers are
explicitly not evidence of current business facts; the assistant must recheck live tools. Existing
read-only navigation actions and evidence are saved with replies. Opening them reuses normal page
authorization and current state.

Still-available legacy session history is imported idempotently. Imported text is marked as
historical; client-provided evidence/actions are discarded. Already-closed old tabs cannot be
recovered. New chat starts a separate context, without inheriting other conversations.

The composer is a single rounded surface with a growing textarea, Enter to send and Shift+Enter
for a newline (IME composition excluded). One focus treatment surrounds the composer, avoiding
the old nested textarea outline. The empty state places the composer near the center; conversations
keep it at the bottom. Responsive sidebar navigation remains available on mobile.

## Validation

API tests cover reopen/follow-up, identity and permission-scope isolation, retry idempotency, busy
and expired attempts, archive/restore, history search and pagination, retrieval beyond the recent
context window, and legacy migration. Migration tests compare ORM metadata against an upgraded
database and exercise upgrade/downgrade. UI checks cover a real provider answer, reload/reopen,
sidebar selection, New chat, composer expansion, and narrow layouts.

References: user-supplied ChatGPT UI screenshots; official
[ChatGPT quickstart](https://learn.chatgpt.com/docs/quickstart) and
[chat organization](https://learn.chatgpt.com/docs/projects?surface=app).
