# KPIs and metrics

> **สรุปภาษาไทย** — ตัวชี้วัดที่ team lead ควรดูเป็นประจำ แบ่งเป็น ผลลัพธ์ธุรกิจ, คุณภาพของการตัดสินใจ,
> การเรียนรู้, ประสิทธิภาพ, และสุขภาพระบบ พร้อมแหล่งที่มาของตัวเลขและค่าเป้าหมายเบื้องต้น

The targets below are proposals to start the conversation. None has been agreed with the team or measured
on real files yet; replace them once a baseline exists.

| Area | KPI | Definition | Source | Proposed target |
|---|---|---|---|---|
| **Outcome** | Mappings approved automatically | mapping versions approved by policy ÷ all mapping versions, all time (a per-fortnight, per-file figure is not computed yet) | `GET /dashboard.automation_rate`, Prometheus `forma_automation_rate` | ≥ 60 % after the first quarter, rising |
| | Time to publish | median hours from first upload to export per round | `history` + exports | ≤ 24 h |
| | Reviews per 100 files | review tasks opened ÷ files; data held for a person opens no task | `review`, Ops | falling month over month |
| | Oldest open review | age of the oldest open review task | `forma_review_oldest_age_seconds` | < 2 days |
| **Decision quality** | Precision per verdict rule | right ÷ decided (accepted unchanged + auto ok) | Settings › Approval policy › calibration report | ≥ 95 % for `accept` outcomes |
| | Automatic approvals edited later | auto_then_edited ÷ auto; rollbacks are not counted yet (R23) | calibration report | < 2 % |
| | Blocks that were real | blocked rounds rejected ÷ blocked | review tasks + audit | high; a low ratio means the wrong-file rule is too eager |
| | Invalid records at export | always 0 by construction | contract gate | 0 |
| **Learning** | Corrections per approval | (alias + constant) ÷ (accepted + corrected) per month | Settings › What FORMA learned | falling |
| | Learned fields used | uses of learned aliases/constants, grouped under the month the lesson was created | same | rising, then stable |
| | Accuracy with learning vs without | `forma eval --replay` on real sequences | eval report | with ≥ without |
| **Accuracy** | Field / critical accuracy on the corpus | eval metrics | `make eval-gate` | 0.996 / 1.00, never lower |
| | Same on real files | eval on `data/real` | eval report | establish a baseline, then never lower |
| | AI vs heuristic delta | `--llm` minus heuristic | eval report | ≥ 0 or the AI is turned off for that stage |
| **Throughput** | Files per minute, rows per second | benchmark | `forma bench`, PERFORMANCE.md | 300 files × 100K rows in ≤ 25 min |
| | Profile / propose / transform p95 | per stage under load | bench report, traces | ≤ 10 s / ≤ 6 s / ≤ 25 s |
| **Reliability** | Failed jobs | jobs in `failed` | Ops, `forma_jobs_queue` | 0 outside incidents |
| | Stuck bulk items | items with no job | batch view `stuck` | 0 |
| | Breaker open minutes | time the AI breaker was open; API process and primary route only (R27) | `forma_llm_breaker_open` | low; backups should absorb outages |
| | Backup age | time since last `forma backup` | System health, audit `backup.create` | < 24 h |
| **Cost** | Estimated AI cost per round | `llm_calls.cost_usd` per round | Prometheus `forma_llm_cost_usd_per_round`; Home shows the all-time total, Ops recent days | budgeted per customer tier |
| **Adoption** | Verdict accepts vs manual approvals | `verdict.accept` audits ÷ approvals | audit | rising |
| | Assistant answers with evidence | replies with ≥ 1 citation ÷ replies | conversations | ≈ 100 % |

## Dashboards

* **Home**: what needs a person, expected deliveries, work-round dashboard, statistics (automation rate,
  records approved or exported, learned from you, estimated AI cost).
* **Settings › Approval policy**: calibration report and proposals.
* **Settings › What FORMA learned**: trend table, learned rows, suggestions.
* **System health**: queue, workers, failed jobs, breaker, spend, slowest rounds.
* **Prometheus** (`/metrics`): queue depth by status, review age, automation rate, breaker, request latency.

## Review cadence

Weekly: automation rate, reviews per 100 files, precision per rule, corrections per approval, failed jobs.
Per release: eval metrics, benchmark delta, risk register. Monthly: cost per round, calibration proposals
applied and their effect, real-file baseline progress.
