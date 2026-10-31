# ADR-048 — The assistant runs code in an isolated sandbox

**Status** accepted · 2026-10 · narrows ADR-002 for the assistant only

## Context

The assistant could search, count and draw the reports the server knows. Anything else (a join across two
datasets, a statistical check, a chart of a new kind, a file to take away) needed a new tool. The owner asked
for what Claude and ChatGPT offer: the assistant writes code, runs it, reads the result and tries again.

ADR-002 says generated code is never executed, and the reason still holds where it applies: a mapping has to be
reproducible, reviewable and safe to run on every delivery. A question in a chat has none of those needs, and its
result is read by a person who decides what to do with it.

Provider-hosted code tools were rejected. They exist for only some providers, the data would leave for the
provider's cloud as files, and FORMA would depend on which model is connected.

## Decision

1. **`run_python`, an assistant tool that runs Python in its own container.** The model writes the code; the
   `sandbox` service in `docker-compose.prod.yml` executes it (`code_sandbox/runner.py`). The tool is not in
   `TOOLS`, so MCP never gets it.
2. **Results stay in the chat.** A run works on copies and its output goes to the conversation. It has no path to a
   mapping, a dataset, the policy or an export: those stay behind ADR-002 and ADR-008. A person who wants a result
   in the data still uses the ordinary review, edit and approval steps.
3. **No network, no secrets.** The container has `network_mode: none`. The API reaches it through one Unix socket on
   a shared volume, so the code cannot reach the API, the database, the object store or the internet. The image
   holds Python, the data and document tools and the runner; no `forma` package and no credentials.
4. **Nothing survives a run.** Each request carries the code and its inputs, each response carries stdout, stderr and
   the files in `outputs/`, and the run directory is deleted. Variables do not carry over. Files saved by earlier
   runs in the chat are stored by the API and sent back as `inputs/previous/`. Pictures a run saves in `look/` reach
   the model's next step and nowhere else, so a model that can see checks what it made before handing it over. A
   small `/tmp` exists because LibreOffice cannot start without it; the runner empties it after every run.
5. **Limits.** 60 seconds, 2.5 GB of address space inside a 3 GB container with no swap, 256 MB per file, 128
   processes, 40 output files and 40 MB of output, 200 MB of input. One run at a time.
6. **An unprivileged run.** The root file system is read-only and all capabilities are dropped except the five the
   runner needs to switch user and clean up. Code runs as uid 20000 with no capabilities and no groups. It cannot
   open the socket directory, signal the runner or write outside its own run folders and `/tmp`. Anything it leaves
   running is killed and reaped when the run ends.
7. **Access is checked on the API side.** Datasets and source files a run asks for go through `ensure_customer`;
   chat files through the chat's owner. Whatever the runner answers is treated as untrusted: file names are cleaned,
   links are not followed, and a file type outside the list only downloads, never displays.
8. **Everything is shown.** The chat shows the code, its output, charts and files for every run, failed ones
   included. The audit log records the actor, a hash of the code, the inputs and the outputs of each run.
9. **On by environment only.** `FORMA_CODE_SANDBOX_SOCKET` switches it on. Dev mode stays zero services and the
   assistant simply has no such tool. There is deliberately no fallback to a plain subprocess in the API, which would
   have the API's network and secrets.

## Consequences

* Rule 2 now reads: AI never executes code in the data path. The sandbox is the one exception, and its results reach
  a person, never a record.
* A hostile cell can still steer the model into writing bad code. The damage is bounded by point 3: the code can
  read only what that run was given, and the only thing it can do with it is print it into the chat, which goes to
  the AI provider like any other evidence.
* The container shares the host's kernel. Claude and ChatGPT use stronger isolation (gVisor or a micro-VM). Recorded
  as R32; `runtime: runsc` can be added to the service where gVisor is installed.
* The image is about 3 GB, built from `deploy/sandbox/Dockerfile`. There is no network at run time, so a library
  that is not in `deploy/sandbox/requirements.txt` cannot be used. The list starts from what Claude's code execution
  documents, with two differences: `wkhtmltopdf` is gone from Debian, so WeasyPrint stands in for `pdfkit`, and
  `reportlab` comes without `pycairo`. It also has to cover every format ingestion and a chat accept, or a run cannot
  open a file FORMA already holds: `.xls` and `.xlsb` need `python-calamine` for pandas and `fastexcel` for polars.
* LibreOffice, Java, Poppler and the other parsers read untrusted files, which adds to what R32 covers. A run's
  address space stays at 2.5 GB, so Java gets a smaller reservation from the runner.
* Prompts grew and the tool loop may take up to 12 turns when the sandbox is on, so such answers cost more.

## How it was checked

From a separate container, against the hardened service: no route to the internet or to `api`, `postgres` and
`minio`; no write outside `outputs/`; no access to the socket, to PID 1's environment, or to root; an endless loop
stopped at its limit; a memory bomb ended in `MemoryError`; a fork bomb stopped at 127 processes and the next run was
unaffected; a daemon that left its process group was killed; a symbolic link to `/etc/passwd` in `outputs/` was not
followed. A live chat ran code on a 391-record dataset and returned a histogram and a CSV.

After the document tools went in, the same limits held: no network, no writes outside the run's folders and `/tmp`,
and a file left in `/tmp` was gone from the next run. Live chats drew a flow, made a PowerPoint and a Word report from
real numbers, and read PDF, a scanned PDF, Word, PowerPoint, email, ZIP and ODT. `.doc`, `.ppt`, `.odp`, `.rtf`,
`.epub`, `.ods`, `.xlsm`, `.parquet`, `.sqlite` and `.tgz` were opened in a run with Thai content. `.rar` and `.msg`
were not, for want of a sample.

A `.xls` and a `.xlsb` saved by Excel, with Thai headers and a formula, went through the socket into a run: `read_excel`
in pandas and in polars returned the same text and numbers from both.
