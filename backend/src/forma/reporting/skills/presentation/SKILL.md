---
name: presentation
description: Compose an editable formal slide deck with source-backed numbers.
metadata:
  version: "1.1.0"
---

# Presentation · 1.1.0
Use create_document with presentation template and pptx format after build_report. Use the approved brand: navy, lime accents, white pages, Noto Sans Thai. Editable native text, tables and charts; no full-slide screenshot as the presentation. Start with scope, follow with evidence, then sources and limitations. Titles must describe the subject without adding numerical claims. Paginate long tables and split long labels; never shrink text until unreadable. Charts must use the same snapshot as the accompanying table. A missing number is unknown. Preserve the original dataset and units. Do not manufacture conclusions to fill a desired slide count. If asked for unsupported narrative or assets, clarify or use run_python with explicit human-review status.

Use brief and sections for an audience-specific presentation: summary cards, findings, comparison panels and proposed actions. Bind numerical claims with {{e5.r0.value}} using actual evidence IDs and row/column references. Cite evidence_ids for each section. These sections precede the complete numerical appendix. Use distinct, descriptive slide titles. Analysis remains human-review material even when its numbers are grounded. For later edits, inspect_document then patch existing section IDs with base_version; unpatched sections and source data are preserved. A section may span multiple slides, so identify its title rather than assuming a physical page number.
