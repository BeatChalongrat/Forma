# ADR-077: Back to the AI after an outage, and where the time per file goes

Status: implemented, 2026-11-21. Tested with a scripted provider that is down, then answers. Not yet run
against the real provider. The reasoning-effort setting is in place but left unset; see "Not measured".

## Context

Two questions came out of the capacity estimate for a 200–300 file month:

1. When the AI is down, a file gets FORMA's own draft and goes to a person (ADR-013, ADR-015). Nothing sent
   it back to the AI when the AI recovered. After a long outage, someone had to find those files and run
   them again by hand.
2. What would make a run faster on AWS without making it worse?

The second question needs to know where the time goes. The 214 provider calls of the three real runs on
the ERP exports (ADR-076, `deepseek-flash`, graph workflow) answer it:

| Measure | Result |
|---|---|
| Input / output tokens per call | 27,700 / 5,300 on average |
| Time per call | 29 s mean, 26 s median |
| Correlation of time with output tokens / input tokens | 0.97 / 0.15 |
| Fitted time per call | 1.4 s + 5.25 s per 1,000 output tokens (about 190 tokens/s) |
| Share of call time explained by output tokens | 95% |
| Calls by output size: under 1k / 1–3k / 3–6k / over 6k tokens | 5 s / 12 s / 24 s / 46 s average |
| Time per role per file: analyst / mapper / execute / reviewer | 49 s / 71 s / 0.2 s / 54 s |

The time goes to the model writing its answer. Reading the input costs little, and FORMA's own work (the
`execute` step runs the mapping on the whole file) is under a second. For `deepseek-flash`, DeepSeek's
documentation says thinking is on by default at effort `high`
([thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/),
[chat completion](https://api-docs.deepseek.com/api/create-chat-completion/), read 2026-11-21). FORMA
sent no `reasoning_effort`, so every call thought at `high`. The usage DeepSeek returns has
`completion_tokens_details.reasoning_tokens`, which FORMA did not record. So the share of the 5,300 output
tokens that is reasoning is not known.

## Decision 1: a fallback goes back to the AI once it answers

`forma.services.ai_retry`, run from the job recovery loop, at most once per `ai_retry_interval_s` (60 s)
in each process.

### Which rounds qualify

- The round's current proposal is a fallback draft: `source: heuristic` and `checks.llm_fallback`.
- The round is in `review` or `blocked`.
- Nobody has acted on its card: the task is `open`. It has not been answered, edited, approved or
  rejected, and it is not waiting for the customer.
- No job for the round is queued or running.
- The fallback is younger than `ai_retry_window_h` (72 h), so an outage over a weekend is covered.

### When they are sent

- **Only when the queue is free.** Retries wait while any upload, pipeline or propose job is queued, so
  catching up fills idle capacity and never delays a new file. At most `ai_retry_parallel` (2) run at once.
- **Only when the AI answers.** Before staging anything, one connectivity probe goes to the primary route
  and each backup key (`services.config.probe_all`, a few tokens). It is sent only when a round is due.
- **Later each time.** The first retry waits `ai_retry_after_s` (300 s) after the fallback was written,
  the second 900 s, the third 2,700 s.

### How many times

- **An outage gets `ai_retry_max_attempts` (3).** This covers timeouts, HTTP 429/5xx, an open circuit,
  a full limiter, the task deadline, and any failure FORMA does not recognise.
- **An answer FORMA rejected gets one.** This covers invalid or truncated output, a refusal, and the
  workflow's own attempt cap, because asking again rarely changes these.
- **A fresh fallback starts over.** The count follows one line of drafts. A fallback that no retry
  produced, such as one from a person running the round again, starts the count at zero.

### The claim and the job

The sweep locks the round, checks it again, and in one transaction (the outbox) writes:

- the job (`retry_ai_mapping`);
- the claim in `ImportRound.summary.ai_retry`: state, job id, attempt, the draft it replaces, and the
  reason for the fallback.

The job locks the round from its first read to its commit. It proposes with the AI and supersedes the old
proposal. It dismisses the old card in the transaction that writes the new one.

- **A person cannot race it.** While the claim's job is live, approving, rejecting, editing or answering
  the old card is refused at once with a 409 that says the AI is reading the file again. The refusal
  comes before the request waits for the round's lock. Answering a superseded card is refused too.
- **Closing the round cancels the job.** The job's commit is fenced. On its next sweep, the recovery loop
  frees a claim whose job has ended without finishing. That attempt counts.
- **A changed round throws the new proposal away.** If the round no longer has the proposal the claim
  replaces (for example, a knowledge revision wrote a new one), the job rolls back and records
  `overtaken`.
- **The AI fails again.** The new fallback replaces the old one, and its card says "AI retry n of N …
  FORMA will try again later" or "… FORMA stopped retrying". The fallback's own explanation says FORMA
  will send the file back unless the person acts first. That sentence is dropped once FORMA has stopped.
- **The policy approves the AI's proposal.** The rest of the pipeline is staged in the same transaction:
  `run_pipeline` transforms the file, and publishes it when the round asked for that. This is how a bulk
  upload finishes.

Nothing is approved by this module. The new proposal meets the same policy as any other.

## Decision 2: record reasoning tokens and allow the effort to be set

- `Usage.reasoning_tokens` comes from `completion_tokens_details.reasoning_tokens` when the provider
  reports it. It goes into the workflow's attempt ledger (`forma_mapping_calls.usage_json`) and the agent
  log. Ledger rows written before this field existed still read.
- `FORMA_LLM_REASONING_EFFORT` (`none`, `low`, `high`, `max`) is sent as `reasoning_effort` to an
  OpenAI-compatible provider. It is unset by default, which keeps the provider's own default; nothing
  changes until someone sets it.

## AWS: what a deployment changes and what it does not

### What it does not change

Hosting FORMA on AWS does not make one file faster: the model's answer is written on the provider's
servers, at the provider's speed. The network path from an AWS region to the provider adds well under a
second to a call of about 29 s.

### What it changes: files in parallel

The production compose file (`docker-compose.prod.yml`: PostgreSQL, Redis, arq workers) maps onto AWS as:

- **ECS on Fargate** for the API and workers;
- **RDS for PostgreSQL** for the database and the workflow checkpoints;
- **ElastiCache for Redis** for the queue and the shared AI limiter;
- **S3** for files (`storage/s3.py`).

With PostgreSQL, AI jobs no longer wait for SQLite's single writer. The number of files in flight is then
set by two settings, which must be raised together:

- **`llm_shared_concurrency`** (4 today). This, not AWS, caps a 300-file run at about 3 hours. DeepSeek
  documents an account concurrency limit of 2,500 for `deepseek-flash`
  ([rate limit](https://api-docs.deepseek.com/quick_start/rate_limit)).
- **Worker threads × replicas.** Raising workers without raising the limiter makes jobs wait for a slot
  inside their 600 s budget and fall back to the draft (see the capacity estimate). Workers can scale on
  queue depth.

### Amazon Bedrock as the model provider

Read 2026-11-21:
[chat completions](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-chat-completions-mantle.html),
[endpoint availability](https://docs.aws.amazon.com/bedrock/latest/userguide/models-endpoint-availability.html),
[regions](https://docs.aws.amazon.com/bedrock/latest/userguide/models-region-compatibility.html),
[quotas](https://docs.aws.amazon.com/general/latest/gr/bedrock.html).

- **Endpoint.** Bedrock serves an OpenAI-compatible Chat Completions API at
  `https://bedrock-runtime.{region}.amazonaws.com/openai/v1` with an Amazon Bedrock API key, so
  `openai_compatible` with `llm_base_url` should reach it without new code. This is not yet tried.
- **Regions.** DeepSeek V3.2 and V3.1 are listed in-region in Tokyo (`ap-northeast-1`) and Jakarta
  (`ap-southeast-3`), not in Singapore or Thailand, and without cross-region inference.
- **Quotas.** 10,000 requests and 100,000,000 tokens per minute for DeepSeek V3.2 per region.
- **Not documented.** Whether `response_format`, `max_tokens` or `reasoning_effort` work for these
  models.

What it would change: the files stay inside AWS instead of going to DeepSeek's own service. What it would
not do is make a run faster or as good by itself. V3.2 is an older model than `deepseek-flash`
(DeepSeek-V4.1-Flash per the [pricing page](https://api-docs.deepseek.com/quick_start/pricing)), and its
speed on Bedrock is not measured. It has to pass the same eleven ERP files before it is used.

## What makes a file faster, in order of expected gain

1. **Less reasoning per call.** Run the eleven ERP files once with `FORMA_LLM_REASONING_EFFORT=low`, and
   once with `none`. Compare values right (420 of 420 now), questions asked, reviewer answers accepted,
   time and tokens with the run at the default. Keep a lower setting only if the quality measures are
   unchanged. With 95% of call time in output tokens, every reasoning token saved is time saved. This is
   a real-model run and needs a go-ahead.
2. **Shorter answers.** The mapper writes the whole mapping each time and the reviewer writes long
   reviews. A repair could return only what changes. This is a prompt and contract change, measured the
   same way.
3. **More files at once** (above). This does not change the time per file, only the run.

## Consequences

- An outage no longer leaves work behind: files are sent back on their own. A person who acts first
  keeps the file, and nobody's answer is overwritten.
- Retries cost model time again, at most three full workflows per file for an outage and one for a
  rejected answer. Each due sweep spends one probe of a few tokens.
- Each process runs its own sweep. Claims under the round lock keep that from sending a round twice. On
  SQLite, the retry, like any AI job, holds the single writer while the model works.
- Default behaviour of the provider calls is unchanged.

## Not measured

- How much of the output is reasoning: the field is recorded from now on, and the next real run will
  show it.
- Quality and speed at a lower reasoning effort, and of DeepSeek on Bedrock.
- A real outage and recovery, and a load test at 200–300 files.
