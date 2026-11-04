# ADR-064 — File actions and a readable account header

Status: accepted, 2026-11-03. Extends ADR-052's delivery lifecycle with file-level actions.

## Decision

Expose **View file**, **Replace this file**, **Move customer** and **Stop using** at each stored
source-file row in file history, work-round contents and the delivery detail page. File edits
require the importer role and access to both customers for a move. The dialog identifies the
selected file, shows old/new names and sizes before a replacement, and explains the effect in
ordinary language. A short editable reason is recorded with the actor.
View file opens source cells immediately, with a sheet selector and an explicit preview limit
of 60 rows and 30 columns. Mapping history explains the recorded source of the mapping and
links to the saved source-column/evidence view; it does not invent missing explanations.

Original SourceFile records and stored bytes remain unchanged. Replacing one source creates a
successor delivery containing the replacement and references to every retained sibling, and
reruns the normal profile/mapping/policy/transform pipeline. Cancelling or moving one source
from a multi-file delivery similarly creates a successor for the retained sources and a separate
cancelled or moved delivery for the selected file. Previous output leaves the work round's ZIP;
new output requires the usual checks. Closed deliveries retain their history. Notes and target
contracts carry forward. Active queued/running jobs block file changes rather than race a worker.

Work-round rows keep their original receipt metadata, link to the current source ID, and display
the current source name and size after replacement. Existing ZIP filenames remain stable. Shared
source references use storage keys; no service reads customer files by constructing paths.

The sticky header uses a 90% background with blur after vertical scrolling. At the top, during
search, during keyboard focus in the header or while the account menu is open, it is opaque.
Text and controls retain full opacity. The account trigger shows a display name, translated role
and dropdown chevron. No work-round selector is added to the header.

The home dashboard no longer duplicates the upload button; file history remains the upload
entry point, including adding files to an existing work round. Both the work-round detail and
upload form show a visible arrow-and-label return link above the title. Links from the work-round
list preserve its search/date filters. Adding files from an open round returns to that round,
with its own return destination preserved. Direct links have an explicit in-app fallback, and
the return link remains available while round data is loading or fails to load.

## Verification and limits

API tests cover retained siblings, immutable originals, history, receipt links, role/customer/file
scope, rejected uploads and processing conflicts. The end-to-end lifecycle test covers both whole
delivery and single-file replacement through the actual deterministic pipeline and ZIP export.
Frontend type/build checks and browser checks cover menu/dialog behavior and account/header states.

Files awaiting initial customer identification do not yet have a SourceFile/delivery; their existing
assignment flow is retained. Downloaded files outside FORMA cannot be recalled. A new result may
require human answers when a retained delivery is incomplete or has an unfamiliar format.
