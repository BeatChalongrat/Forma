---
name: formal-report
description: Create an evidence-backed executive, monthly or incident report.
metadata:
  version: "1.0.0"
---

# Formal report · 1.0.0
Ask for audience, period/time zone, customers and intended decision when unclear. Use build_report to compute numbers; a profile sample is never the population. Load this skill before create_document. Select template executive, monthly or incident. Use source evidence IDs, never send replacement rows, totals or percentages. Keep currencies and units separate. Null means unknown, never zero. All formats use the same frozen snapshot. Partial evidence must be marked partial in every format. If a report needs calculations unsupported by build_report, use run_python for exploration, describe its limitations, and do not claim it passed the formal report numerical gate.

Use short, factual titles. Incident reports must distinguish measured symptoms from a confirmed cause. Ask for missing cause/owner/next action rather than inventing them. Request Word, PDF and/or PowerPoint in create_document. The queued job runs without an LLM; return the job receipt, never say it has finished until status is done. Users can stop or retry it from the report card.
