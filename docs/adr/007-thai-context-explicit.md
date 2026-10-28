# ADR-007 — Thai context is explicit, tested operations

**Decision** BE/CE years, Thai months and digits, ฿/comma numbers, VAT inclusive/exclusive, Thai trade
units and pack sizes are implemented as named DSL ops with ≥ 40 unit-tested cases. The mapper records every
assumption (e.g. VAT-inclusive when the header is silent) as evidence and an open question.

**Consequences** No implicit locale magic; reviewers see what was assumed; the LLM is told which op to use.
