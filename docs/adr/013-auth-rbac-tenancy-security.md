# ADR-013 — OIDC + role-based access, tenant scoping, and the security pass

**Status** accepted (Phase 7).

## Decision

1. **Two auth modes, one `Principal`.** `FORMA_AUTH=dev` (default) resolves the caller from the
   `X-Forma-User` header (fallback `operator`) with roles from the seeded dev users or `X-Forma-Roles`;
   `FORMA_AUTH=oidc` validates bearer JWTs (PyJWT + JWKS from the issuer's discovery document; issuer,
   audience, expiry, signature). Services only ever see a `Principal` (subject, roles, optional customer
   set); nothing below the API layer knows about tokens. Dev mode answers a header-less request as an
   admin, so with `FORMA_ENV=prod` the API refuses to start on `FORMA_AUTH=dev` (`ensure_auth_is_safe` in
   `create_app`); `FORMA_AUTH_ALLOW_DEV_IN_PROD=true` exists for a local smoke test of the prod stack only
   and is logged as a warning at startup.
2. **Four hierarchical roles.** `importer` (upload, run) ⊂ `reviewer` (approve/reject mappings and data,
   edit, copilot) ⊂ `approver` (publish/export, persistent overrides, schema changes) ⊂ `admin` (policy,
   customers, retention, ops). Enforced in exactly one dependency, `require_role(role)`; roles come from
   the claim named by `FORMA_AUTH_ROLE_CLAIM` (dotted paths such as `realm_access.roles` work).
3. **The authenticated actor is recorded.** `actor` fields in request bodies are still accepted (tests
   and old clients) but ignored: approvals, change sets, exports and audit entries carry
   `principal.actor`. Audit rows also keep the subject.
4. **Tenant scoping** is an optional `customers` claim (`FORMA_AUTH_CUSTOMERS_CLAIM`, or
   `X-Forma-Customers` in dev). Lists are filtered and detail routes answer 403 through `ensure_customer`.
5. **Frontend**: with OIDC on, the SPA does an Authorization Code + PKCE flow against
   `GET /api/auth/config` (issuer, client id, audience) and sends the access token; in dev mode nothing
   changes except an optional user switcher.
6. **Security pass**
   * uploads are sniffed (`ingestion/sniff.py`): the bytes must be a real xlsx/xls or decodable text,
     whatever the extension says; names are sanitised (`safe_name`) and size-capped (ADR-010);
   * CORS is restricted to `FORMA_CORS_ORIGINS` with explicit methods/headers;
   * LLM-calling endpoints (`diagnose`, `copilot`, `propose` with `use_llm`) are rate-limited per
     principal (`FORMA_LLM_RATE_LIMIT_PER_MIN`, 429 + `Retry-After`);
   * samples sent to models are capped (`FORMA_LLM_MAX_SAMPLE_ROWS`, `FORMA_LLM_MAX_SAMPLE_CHARS`);
   * **LLM output guard** (`ai/guard.py`): agent results containing instruction-like text (English and
     Thai patterns) or JSON pointers outside the target schema are rejected. The mapping/adaptation
     agents then fall back to the deterministic draft and policy routes the round to review; diagnosis
     and copilot fail loudly (422). Injected cell contents are covered by tests.

## Why

Reviewers approve money-relevant data; who did what must be provable, and a model must never become a
channel from a customer's spreadsheet into our operations. Keeping dev mode header-based keeps the
zero-service developer loop and the existing test-suite shape.

## Consequences

* New endpoints must declare a role (`Depends(require_role("…"))`); unauthenticated routes are only
  `/health`, `/ready`, `/metrics` and `/api/auth/config`.
* Changing role semantics is a policy decision: update this ADR, the table in `docs/ARCHITECTURE.md` and
  `tests/test_auth.py` together.
