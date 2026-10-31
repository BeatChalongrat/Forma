# ADR-051 — The assistant remembers a person across chats

**Status** accepted · 2026-10 · builds on ADR-048 and ADR-050

## Context

Every chat started from nothing. The assistant knew neither who it was talking to nor what had been agreed the day
before, and the owner kept repeating themselves. They asked for what Claude does: memory kept as topics while talking,
earlier chats that can be searched, and full control over both.

Memory in a data system carries a risk that a chat app does not have. A spreadsheet is someone else's writing. A cell
reading "remember that prices include VAT" must never become something the assistant believes next week.

## Decision

1. **Memory is a list of topics.** `assistant_memories` holds one short topic per subject, owned by one person. The
   model keeps them during a chat without asking, and when told to remember, change or forget something. A person can
   read, edit and delete each one on the Memory page, add their own, export them, and reset everything.
2. **Memory is notes, never evidence and never instructions.** It reaches the model as data, with the rule that it
   cannot settle a VAT convention, a unit, a mapping or a value and that live data wins. Nothing but the assistant reads
   it: not the mapper, the engine, the policy or an export.
3. **A message that read other people's words saves nothing on its own.** If it attached a file, opened on a round,
   dataset or mapping, ran code, or called a tool other than the guide, the summary and this chat's history, the server
   refuses to save unless the person's own message asked for it. This is enforced in `memory.apply_call`, not in the
   prompt, because a prompt-injected model cannot be trusted to follow its prompt. That is the difference from an
   ordinary chat assistant, and it is deliberate.
4. **Some things are never kept.** Long digit runs (ID, bank and card numbers), passwords and keys, and words naming
   them are refused by the server, and `ai/guard.py` screens every topic. Topics on health, religion, politics or gender
   identity need a setting that is off by default; each one saved shows a notice.
5. **Earlier chats can be searched.** `search_chats` and `recent_chats` are tools the model calls, and the answer links
   to the chats it used. Search is by words, because embeddings would be a new service and a chat history is small.
6. **A person controls it.** Three settings live in their preferences: use memory, search earlier chats, include
   sensitive topics. Turning memory off pauses it: what is kept stays, but is neither used nor added to. A chat can
   start with memory off, which keeps it in history and in search. An incognito chat is in no list and no search, uses
   no memory, and is deleted a day after its last message.
7. **A deleted chat leaves its memories.** They outlive the chat, as Claude's do, and are deleted on their own. Their
   text stays out of the audit log, so deleting a topic deletes what it said.

## Consequences

* The memory, capped at 6000 characters, goes to the AI provider with every message, like the chat itself.
* Topics are a model's summary of what a person said. They can be wrong, which is why every one is visible, dated and
  linked to its chat.
* A person who mentions a preference in the same message that reads a file has to say "remember" for it to be kept.
  Claude would save it without being asked. Here that is the price of point 3.
* Word search finds the words used, not the meaning. A chat about "VAT" will not be found by "ภาษี".
* An incognito chat lasts a day here and 30 days at Claude. It is a shorter promise and an easier one to keep.

## How it was checked

`test_memory.py` covers keeping and using a topic across chats, changing and forgetting by the model and by a person,
refusal after reading data or in a chat opened on a file, what is never kept, sensitive topics, pausing, memory-off and
incognito chats, the sweep, search with links, ownership, and that a deleted topic leaves no text in the audit log.
