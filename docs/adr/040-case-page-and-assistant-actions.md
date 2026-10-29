# ADR-040 — The case page, and an assistant that acts after a person confirms

**Status** accepted · 2026-10-30 · phase 1 implemented. Extends ADR-028 (workspace), ADR-029 (assistant),
ADR-036 (verdict).

## Context

A file's work was spread over five pages: the round (steps and history), the review (questions), the Studio
(fields), the Data Editor (records) and the assistant (questions about any of them). The verdict (ADR-036)
gave each file one conclusion, but it lived on the review page only; the round page still said "Answer
questions". The assistant could explain a file in detail and then could not do the one thing the
explanation led to.

## Decision

1. **The round page is the case.** The full verdict card — conclusion, confidence, the decisions only a
   person can make, the evidence, the one-click accept — sits at the top of every round page, for the mapping
   stage and the data stage alike; the five-step tracker and the file / version history stay beneath it in
   disclosures. The review page, the Studio and the Data Editor remain the places for the long work and are
   reached from the card's evidence links. "Ask about this file" opens the assistant in the round's context.
2. **The assistant proposes actions; a person performs them.** Beside the navigation actions of ADR-029,
   `navigation_choices` now builds one *do* action per inspected round — `verdict.accept` with the round id
   and the verdict's digest — only when the verdict's outcome is `accept`, it asks no decision, and the
   principal may review. The model picks the action by id like any other (never a URL or a payload); the UI
   renders it as a card with a preview of what will happen and a **Confirm** button; the click calls the
   ordinary `POST /rounds/{id}/verdict/accept`, so the policy still decides, a stale digest is refused, and
   the audit log records the rules that fired. The model is told the action needs confirmation and may say
   so; it cannot perform it.
3. **Nothing else is writable from the chat.** Answering questions, moving a file to another customer and
   editing data stay on their pages; they need the evidence in front of the person. The closed set of act
   types is `verdict.accept` for now.

## Consequences

* One page per file carries the conclusion and the action; the queue, Home and the batch rows already carry
  the conclusion, so most files are settled without opening anything else.
* The assistant becomes an agent in the only sense that is safe here: it can propose a server-built action
  tied to specific evidence, and a person confirms it. Every path a model can trigger is the same path a
  button uses.
* Not in this phase: act actions for `answer`, `assign_customer` and data-stage acknowledgements; docking the
  assistant beside the case; a case page for bulk-upload items that have no round yet.

## Verification

`tests/test_assistant_navigation.py`: an inspected round whose verdict says accept yields one `act` choice
with the digest, none when the verdict needs a decision or the principal cannot review; the act is a server
object (no URL). Frontend build; live check on the demo: the round page shows the card, the assistant's
reply shows the confirm card, confirming approves the mapping.
