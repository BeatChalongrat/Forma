# ADR-028 — Focused, responsive operations workspace

**Status** accepted · 2026-10-29

## Context

Operators need a clear next action without learning the pipeline's internal vocabulary.
Fixed-height status chips wrapped outside their backgrounds, and permanently visible
side panels squeezed the mapping and data workspaces. The requested visual direction
combines aCommerce's navy, blue and lime palette with quieter controls and less decoration.

## Decision

* Keep the existing bundled aCommerce logo and fonts. Use flat navy primary actions,
  restrained blue selection states and a small lime brand accent. Retain distinct warning,
  error and success colors; support light and dark themes.
* Status chips grow with their content; short labels stay together. Tables scroll inside
  their own sections. Page headings, actions and toolbars reflow rather than clip.
* Collapse desktop navigation to a rail; below 900px use an accessible navigation drawer.
  Upload actions stay in Home and Imports; navigation does not duplicate the upload button.
* Home prioritizes uploads, actionable deliveries and recent results. Statistics and help
  are progressive disclosures. Load failures must not look like an empty, successful queue.
* Mapping and dataset inspection panels open on demand. At 1440px and above they can dock;
  below that they are dialogs. Only one mapping drawer is shown at a time. A native view
  selector makes every inspection view discoverable without a horizontally hidden tab.
* Mapping changes show approved/proposed sources and before/after examples. Technical
  expressions and round histories remain available in disclosures. API identifiers,
  schema-driven field discovery and policy gates remain the source of truth.
* Modals, drawers and command search trap keyboard focus, close with Escape and restore
  focus to the opener. Editor shortcuts ignore controls and dialogs. File selection and
  import navigation have keyboard-operable controls.
* No new illustration assets are needed in the operational UI. No backend contract,
  transformation, approval or publication behavior changes.

## Validation

Use the frontend build and lint plus browser checks against existing demo records.
Check Home, Imports, a round, Mapping, Data Editor and Upload at desktop and narrow widths,
both themes, long Thai filenames, `Missing columns`, panel toggles, dialog focus wrapping,
Escape and focus return. Do not approve, publish or edit stored data during visual QA.
Development can target the existing local Docker API with
`FORMA_DEV_API=http://localhost npm run dev`; the default remains port 8000.

Production rollout is limited to the local `web` service; API, workers and data volumes
are not recreated. Known existing bundle-size and lint warnings are recorded separately
from functional or type errors.

### Verification

- TypeScript and Vite production build pass locally and in the web image; oxlint reports no errors
  (two pre-existing warnings remain; the application chunk is still above 500 kB).
- Home, Imports, Round, Mapping, Data Editor, Upload and the command search were checked at 1440, 1280,
  1024 and 390 CSS-pixel widths in both themes: status chips no longer clip, narrow viewports have no
  horizontal document overflow, the editor grid keeps a usable height with local table scrolling.
- Dialog focus wraps with Tab / Shift+Tab, Escape closes, focus returns to the opener; resizing never shows
  two mapping drawers at once; the Thai command search opens Upload with Enter.
- The web image was redeployed alone; `/ready` reported database, object storage and queue healthy and no
  stored records were changed during the checks.
