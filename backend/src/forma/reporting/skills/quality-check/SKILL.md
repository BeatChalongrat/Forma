---
name: quality-check
description: Check numerical grounding, language, pagination and delivery status.
metadata:
  version: "1.0.0"
---

# Quality check · 1.0.0
The formal renderer validates source snapshots and uses exactly the same values in every format. It renders Office documents in LibreOffice, checks page text and PPT geometry, preserves a machine-readable snapshot, and emits a separate quality receipt. Automatic checks are not human visual approval or verification of business meaning. Do not say 'AI inspected every page' just because images were sent to a model. Missing renderer, failed checks, unsupported content or excess pages must fail explicitly. Do not silently truncate or publish a partially generated pack. Final Word/PowerPoint appearance should be checked by the recipient in their application. Every revision keeps a new snapshot/version. Retry retains the same snapshot; 'latest data' requires a new request.
