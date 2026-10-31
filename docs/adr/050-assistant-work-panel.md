# ADR-050 — What the assistant makes opens beside the chat, with its versions kept

**Status** accepted · 2026-10 · builds on ADR-048 and ADR-049

## Context

The assistant could write a Word report, a deck or a chart, but the result was a file link at the end of a code run.
To see it you downloaded it; to change it you described the file from memory; and a second attempt left two files with
nothing to say which was newer. A flow came back as a picture nobody could edit. The owner asked for the way Claude
works: the work opens in its own window beside the conversation.

## Decision

1. **A work and its versions are records.** `assistant_artifacts` holds one row per work in a chat and
   `assistant_artifact_versions` one per version. Versions are append-only, like mappings and datasets: a change or
   bringing an old version back adds the next number, with `parent_id` and, for a restore, `restored_from`.
2. **One message makes one version.** A model that checks itself saves several drafts in a message. Files become works
   when the message ends, last save of each name wins, and a flow saved twice in one message replaces its own draft.
   Nobody sees a version the model threw away, and identical bytes add nothing.
3. **The file name ties a file's versions.** To change a file the model reads it from `inputs/previous/` and saves it
   under the same name. There is no new tool to learn and no id to get wrong.
4. **A flow is data, not code and not a picture.** `save_flow` takes nodes and edges, checked against a schema; FORMA
   lays them out and draws them in the page. It runs nothing, so it works with the sandbox off, and a person can pick
   one box. The tool is not in `TOOLS`, so MCP never gets it.
5. **Previews are made in the sandbox.** A file a model wrote is untrusted, and only that container opens one.
   LibreOffice and Poppler turn Word, PowerPoint and PDF into page images, kept as attachments of kind `preview` so a
   run never receives one as an earlier file. Spreadsheets become cells, with formulas calculated. A preview that
   fails leaves the file downloadable and says so.
6. **A web page gets no origin and no network.** It runs in an `iframe` with `sandbox="allow-scripts"` and a content
   policy that blocks every request. It cannot read the app, call the API or send anything out.
7. **A work says what it rests on.** Each version keeps when it was made and a mark of the data in the account's scope.
   If the mark changes, the panel says the data has moved on and offers to make it again. Nothing is rewritten by
   itself. A work that used no customer data says that. A version that read no data of its own keeps what the version
   before it rested on: changing a title does not make old numbers new.
8. **What the user picks goes to the model as `working_on`.** A box, a page, a slide or a cell becomes `target`. It is
   context for one message; it changes nothing by itself.
9. **Works are listed with the chats, not on a page of their own.** The assistant's part of the sidebar has a Work tab
   beside chat history, listing every work across chats, newest change first and searchable by name. A work is read and
   changed in its chat, so a list that leads there is all that is needed to find one.

## Consequences

* The results still reach a person and never a record: ADR-048's line holds. Nothing here touches a mapping, a dataset,
  the policy or an export.
* A message that made files takes a few seconds longer, because previews are made before the turn closes.
* Direct editing inside a document or slide is not included: it needs an editor in the browser. A person picks a place
  and asks; only a flow's structure is FORMA's own.
* Doc and deck previews are pictures, so a person picks a page rather than a sentence.
* Previews show LibreOffice's rendering. Fonts and spacing differ a little in Word and PowerPoint.
* Sheet previews stop at 8 sheets, 200 rows and 30 columns, and page previews at 30 pages. The file has everything.

## How it was checked

`test_artifacts.py` covers versions, one version per message, restore, previews kept apart from run inputs, a failed
preview, access through the chat, deleting with the chat, and staleness. Live chats made a flow, a deck, a spreadsheet,
a report and a web page. A picked box changed a flow and a picked slide changed a deck, with the rest left as it was,
and an old version came back as the next number. A page in the same frame and policy could not reach the API, the
network, the parent page, cookies or storage, and could not load an image from outside.

The first live test found a model saving the same flow again and again until its turns ran out, because the open work
was still in front of it. A successful save now says so, the tool is withdrawn after two saves, and a message that
made something never reports failure.
