# ADR-025 — Backup AI keys and model choice

**Status** accepted · 2026-10

**Context** The web app held one AI key, one provider and one model (ADR-017). When that key hit a rate
limit, ran out of credit or was revoked, the circuit breaker opened and every round fell back to the
deterministic mapper until someone fixed the key. Hard errors such as HTTP 401 were also retried with
backoff before the call gave up, because the retry helper retried every `RuntimeError`. Changing the
model meant typing its exact name under advanced settings.

**Decision**

1. **Backup keys.** Settings → AI keeps up to four backup keys, each with its own provider, model and
   optional gateway URL, in an order the admin sets. A key is tried with one tiny call and saved, encrypted
   with `FORMA_SECRET_KEY`, only when that call works (like the main key). Only the last four characters
   are ever shown, and the audit log records the provider and model but never the key
   (`settings.ai.backup.add|update|remove|order`). They are stored in the `ai` app setting, so no new
   environment variable or migration.
2. **Fail-over** (`LLMClient._call_provider`). A call goes to the main key first, then to each backup in
   order. A route fails over on any provider or transport error, or when its circuit is open. Invalid JSON
   is not a provider failure: it is retried on whichever route answered (`max_retries`). When every route
   fails, the agents fall back to the deterministic draft and the policy routes the round to review,
   unchanged (`llm_fallback=True`).
3. **One breaker per route** (`breaker.get_breaker(name)`). A dead key opens only its own circuit, so later
   calls skip it at once and go to the next route. Hard errors (401, 400, a refusal) count against the
   breaker but are no longer retried. Transient errors (timeouts, 429, 5xx) are still retried with backoff.
   The `forma_llm_breaker_open` gauge follows the main key.
4. **Cost and provenance follow the answering route.** `Usage.model` is the model that answered, so the
   LLM ledger prices it correctly. A mapping written by the AI records that model in `meta.model`.
5. **No main key.** If the main provider has no usable key but a backup does, the first backup becomes the
   primary. The Settings page says so.
6. **Model choice.** Every model field is a list of the models FORMA knows for that provider
   (`llm.model_catalogue()`, from the pricing table), plus "Other model…" for any other name: a gateway
   model, or a model released later. The main model can be changed on the connected card without opening
   advanced settings. "Test all keys" probes the main key and every backup.

**Consequences** A revoked or exhausted key no longer turns AI off for every round; the next key answers,
and the cost ledger shows which model did. Backups add no extra calls while the main key works. Every
provider still goes through the same guard, validation and policy. A backup is only an alternative route
to a model, never a way around review.
