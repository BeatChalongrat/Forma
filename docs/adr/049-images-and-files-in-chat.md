# ADR-049 — Images and files in an assistant chat

**Status** accepted · 2026-10

## Context

Customers send screenshots of their ERP. Nothing in the assistant could take one: a message was text, and neither
provider call could carry a picture. The owner wants images read whenever the connected model supports it, and
nothing to break when it does not.

Provider catalogues do not say whether a model reads images, and a gateway can serve anything under any name.

## Decision

1. **Files belong to a chat.** `assistant_attachments` holds uploads and the files code runs produce, in object storage
   under the chat. A message refers to uploads by id. A file is only reachable through a chat its owner can open, and
   it never becomes a record, a mapping or an export. ADR-043 still holds: images in an Excel file are not source data.
2. **The bytes decide the type.** PNG, JPEG and WEBP are recognised from their content; anything else by its extension,
   from `attachments.DATA_TYPES`: tables, documents and slides, text and code, other pictures and archives. The page
   gets that list from `GET /assistant/status`. Limits: 5 MB per image, 25 MB per data file, 5 files per message. Only those three image types
   display in the page, with `nosniff` and a sandboxed content policy; every other file downloads.
3. **Ask the model.** `POST /settings/ai/vision-test` shows each connected model a small picture with two random
   colours and asks which is which. The answer is stored for that exact provider, gateway and model, so changing any
   of them clears it. A refused image means no; an unreachable model leaves the result as it was.
4. **Images go only to models that passed.** `LLMClient` filters its routes, so a backup that cannot see never answers
   as if it had. With no such route the reply says the assistant could not answer. When no connected model reads images
   the assistant is told so, and it says so rather than guessing.
5. **What an image shows is data.** The prompt says text in an image can never change the rules, and that a reading
   from an image is reported as such: it does not confirm a VAT convention, a unit, a mapping or a value.
6. **Context.** The four most recent images in a chat go with each model call in the evidence-selection phase, because
   people refer back to them. The writing phase works from its outline and does not receive them. Files attached to a
   chat are also inputs to code runs (ADR-048).
7. **An answer rests on evidence.** Attachments are an evidence item the model can cite, and an answer with no citation
   is sent back to the model to fix. Before this, an answer from an image alone fell through to the "AI is unavailable"
   text.

## Consequences

* Images go to the AI provider as uploaded, metadata included. Recorded as R33.
* Each image in a call costs tokens, on every call of the tool loop.
* The composer shows in plain words what the connected model can do. The paperclip accepts images only when a model
  reads them, and data files only when the sandbox is on.
* A new chat is committed before its creation is answered: the file upload follows immediately and could otherwise
  arrive before the row is visible.

## How it was checked

`deepseek-flash` passed the test through a live call. A Thai price list drawn on a canvas, attached in the web app,
was read correctly, including the header saying the prices exclude VAT.
