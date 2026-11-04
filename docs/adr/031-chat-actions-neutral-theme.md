# ADR-031: Chat actions and a neutral dark theme

Status: accepted; extends ADR-030 and the workspace theme in ADR-028.

The assistant navigation and collapsible history live in a Conversations section after Help.
Each chat and the active conversation header expose the same ellipsis menu: rename, pin/unpin,
archive/restore, delete. Menus render above scroll containers, fit the viewport, support keyboard
navigation and return focus to the trigger. Rename and delete use the existing dialog focus stack,
including inside the mobile navigation drawer.

Pins persist on AssistantConversation, with an Alembic migration that defaults existing rows to
false. Lists sort pinned conversations first, then by updated time and stable ID. Archived items
remain in a separate view and retain their pin for restoration. All writes use existing account
and access-scope checks. Deleting requires an explicit UI confirmation, removes only that chat's
turns and conversation, and records actor/ID in the audit log. It does not remove customer files,
imports, mappings, datasets, or the audit ledger. An atomic database update serializes deletion with
message lease acquisition; an active reply blocks deletion. There is no public sharing or chat lock.

Dark mode uses neutral charcoal surfaces: sidebar #181818, workspace #202020, popover #262626,
selection #303030, primary text #ededed, secondary text #b3b3b3. Light mode retains the existing
aCommerce navy identity. Action foreground/background tokens are paired independently of heading
colors; steps, send buttons and upload numbers use those tokens. Completed steps use a contrasting
check mark on a success surface. Light success/warning/error text is darker, and disabled buttons
retain readable labels rather than fading the whole control.

Preference reads bypass the browser HTTP cache so opening another page loads the last saved theme.

Validation covers schema/model agreement, pin ordering and authorization, delete ownership and
turn cleanup, deletion during an active reply, build/type checks, and live desktop/mobile UI checks
in both themes. Theme contrast is checked against actual surfaces, including cards and selected rows.

Verification: the conversation and migration test suites, Ruff and strict mypy pass. Browser checks
covered rename, pin after reload, archive and restore, delete confirmation and cancel, keyboard focus and
the 390 px mobile drawer. Text, action and status colour pairs measure at least 4.6:1 in light mode and
6.3:1 in dark mode against their actual surfaces.

Assistant icon refinement: use Lucide MessageCircleMore consistently for the sidebar,
review-page assistant links and assistant feature catalogue. The sidebar uses a 20px,
1.75px stroke rounded chat outline; currentColor follows the existing theme/selection
tokens. Keep the new-chat compose icon and history disclosure separate. No behavior changes.

2026-11-03 refinement: the shared assistant icon is now Lucide MessagesSquare, using
currentColor without sparkle or colored decoration. The full-width Assistant heading
toggles the sidebar's New chat, search, history, work and archive controls, with a
chevron and aria-expanded/aria-controls. Expansion is remembered locally across reloads;
compact navigation retains a direct link to the assistant. Hidden lists stop polling.
