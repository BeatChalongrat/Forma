# ADR-047 — Verdict decisions, accounts with no role, and tenant scope beyond owned rows

## Context

A check of the documentation against the code found four defects, recorded as R19, R26, R28 and R29:

* the VAT decision on the verdict card pointed at the price field, so saving it returned 422;
* an `accept` outcome ignored the decisions listed on the card;
* a valid token with no FORMA role could call every read that uses `current_principal`;
* jobs, dashboard figures, learning statistics, calibration, audit and ops were not limited to the caller's
  customers.

## Decision

**1. A VAT decision is asked about the VAT field.** When a data check reports `vat_added` or `vat_removed`,
`review_questions.questions()` always includes the field with semantic `price.vat_included` in the same
collection, even if its confidence would otherwise keep it out. The verdict's decision uses that question's id
and kind, and carries the field's `semantic` so the web app needs no guessing from the id. If the schema has no
such field, or the mapping does not map it, nothing can be saved, so the outcome is `fix_mapping` and no decision
is listed. The default follows the direction of the move and says so; it is not presented as a Thai convention,
because a move down would contradict it.

**2. Nothing is accepted past an open decision.** A verdict with `must_decide` items is `answer`, whatever rule
fired. `verdict_flow.accept` refuses `accept` with 409 if decisions are listed, and the card disables the button.
So a mapping saved from answers can finish, a new `answered` rule gives `accept` when the current version is a
human edit with no questions and no open questions. `RULES_VERSION` moves to `2026.10.4`, so stored verdicts are
recomputed on the next read.

**3. An account needs a FORMA role.** `authenticated_principal` answers who is calling. `current_principal`
depends on it and refuses an identity with no role with 403 `forbidden`. Only `GET /me` uses the first, so the
web app can tell a signed-in person why they see nothing. Public endpoints are unchanged.

**4. Scope follows the customer behind the row.**

| Surface | Principal limited to some customers |
|---|---|
| `GET /jobs`, `/jobs/{id}`, `POST /jobs/{id}/retry` | only jobs of their rounds; any other job is 404 |
| `GET /jobs/batch/{id}` | their part of the batch; 404 if they have no job in it |
| `GET /dashboard` | every figure counts their customers; AI calls and failed jobs with no round are left out |
| `GET /learning` | statistics and learned rows for their customers; no promotion suggestions |
| `DELETE /learning/{id}` | needs that row's customer; a promoted alias belongs to everyone |
| `GET /jobs/stats`, `/ops`, `/audit`, `/calibration`, `POST /calibration/apply`, `POST /learning/promote` | 403 |
| assistant tool `runtime_configuration` | no approval policy |

Views that cannot be split by customer stay whole and call `ensure_all_customers`.

## Consequences

* A file's VAT question appears in the review questions as well as on the card; both save the same answer.
* A deployment with an identity provider must give each person a role before they can use the web app.
* A customer-scoped admin can still change settings shared by every customer: AI connection, approval policy,
  default format, customers and target formats. This is not part of R19 and is tracked as R30. Do not give the
  admin role to a customer-scoped person until it is closed.

## Verification

`backend/tests/test_tenant_scope.py` covers decision 4. `test_verdict.py` covers decisions 1 and 2, including
saving the VAT decision from the card and finishing the round. `test_auth.py` covers decision 3. Each of these
tests failed before the change.
