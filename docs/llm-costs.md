# LLM cost - what a case costs, and how it is counted

Sub-project 19 (`docs/superpowers/specs/2026-09-27-llm-costs-design.md`). The API is
`docs/api.md` §10; this file explains the numbers behind it: the prices, what is and is not
counted, where it is stored, and what the three §0 scenarios cost.

## 1. Prices and the formula

The model is `gpt-5.6-luna`, for the agent's four calls (§18.5: Intent, Safety, Planner,
Response Evaluator) and for the document-service's one call per upload (a text classification or
a vision call). The owner's prices, per 1M tokens:

| | USD per 1M tokens |
|---|---|
| Input | 0.20 |
| Output | 1.20 |

The cost of one attempt, in `Decimal` (money never goes through a float), rounded half-up to 8
decimal places:

```
cost_usd = input_tokens × 0.20 / 1,000,000 + output_tokens × 1.20 / 1,000,000
```

**Cached input tokens are billed at the full input price.** The owner gave two prices, not a
cached-input discount, so this is the upper bound. The cached count is stored on its own column
(`cached_input_tokens`, a part of `input_tokens`, not in addition to it) for a later discount.

The prices live in `backend/hospital_agent/llm/pricing.py` (`PRICES`). A model the table does not
know has no price: its rows keep their token counts, and their cost is `NULL`, shown as
"מחיר לא ידוע" - never guessed.

### Changing a price

Set `LLM_PRICE_INPUT_PER_MTOK` and/or `LLM_PRICE_OUTPUT_PER_MTOK` in `.env` (USD per 1M tokens,
a plain decimal such as `0.25`); `docker-compose.yml` passes both to the backend. They override
the price of the configured model (`OPENAI_MODEL`, default `gpt-5.6-luna`) only. A model the table
does not know is priced only when both are given.

- A value that is not a finite decimal, is negative, or is above 1000 per 1M tokens is refused:
  the table's price stays, and the log says `llm_price_override_invalid name=<variable>` (a code,
  never the value). An explicit `0` is a valid price.
- The prices are read once, when the backend starts. Restart it after a change.
- A new price applies to new rows only. Each row stores the price it was computed with
  (`price_input_per_mtok`, `price_output_per_mtok`), so earlier cases keep their cost.

## 2. What is counted

**One `llm_usage` row per LLM attempt**, not per successful answer:

- **The agent's calls** (`source` `agent`): `Intent`, `Safety`, `Planner` and `Evaluator`, every
  attempt the Agent Orchestrator makes on a case's behalf, including the Classifier's two threads
  and the Response Evaluator's separate process (its parent records for it).
- **The document-service's call** (`source` `document_service`): `DocumentClassify` for a text
  PDF, `DocumentVision` for a JPEG/PNG photo or a scanned PDF. The document-service reports it as
  `llm_usage` on its `201` answer, and the Session Service records it under the case when the
  patient uploads (`upload_pdf`) or replies with a document (`reply_pdf`).
- **Failed attempts too.** An answer that arrived but was unusable (unparsable, schema-invalid)
  was billed, and its row carries its tokens and cost. An attempt that billed nothing we can see
  (an API error, a dead Evaluator worker) still gets a row, with its outcome code and `NULL`
  tokens and cost - so `calls` counts attempts, and a retried call shows every try.
- `outcome` is `ok` or the same code the Application Log line carries (e.g. `schema_violation`,
  `api:APITimeoutError`, `worker_died`).

## 3. What is not counted

These tokens may have been billed by OpenAI but never reach `llm_usage`. The totals are therefore
a lower bound on the invoice, never an overstatement.

- **A document-service `503` after its call.** When the classification ran but the upload then
  failed on `storage_unavailable` or `database_unavailable`, the `503` carries no `llm_usage`, so
  those tokens are lost (the document-service README's "known accounting gap").
- **A timed-out upload answered as a duplicate.** If the Session Service's request times out after
  the document-service already classified and stored the file, the retry is answered
  `DUPLICATE_DOCUMENT` with `llm_usage: null` (a duplicate makes no call). The first request's
  tokens were billed and are not recorded.
- **Timeouts the provider may bill.** An `APITimeoutError` (or any other API error) has no
  `usage` to read, so its row has `NULL` tokens and cost - but OpenAI may still have billed the
  work it did before the timeout. The same holds for the document-service's
  `503 classifier_unavailable`, which records no row at all.
- **The D33 evaluation runs** (`python -m eval.d33`, and `--live`). They run outside any case, with
  no usage scope, so nothing is recorded. `obs.golden` and the tests record nothing either
  (FakeProvider, no scope).
- **A failed usage write.** If the row cannot be written (the database is down, a constraint
  refuses it), the log says `llm_usage_write_failed error=<ExceptionType>` and the case goes on.
  See §4.

## 4. Where it is stored, and who sees it

- **Table `llm_usage`** (migration `0008_llm_usage.py`): `case_id` (FK `cases`), `source`, `call`,
  `model`, `outcome`, the three token counts, the two prices, `cost_usd` and `created_at`. A row
  holds no request text, prompt, answer or patient field: only a case id, codes, counts and money
  (§12.3). `hospital_app` has `SELECT, INSERT` only (append-only, like `audit_log`), and
  `hospital_reader` has nothing.
- **Written outside the case's transaction.** Each row is written in its own short transaction,
  never inside `StateManager.apply`. It adds no audit row, so the golden traces stay 35/4/54.
- **Bookkeeping, not a safety control.** A failed write is logged and the case continues. This is
  the one place where "fail closed" does not apply, and it is recorded in
  `docs/spec_corrections.md` row 94. No State, guard, policy decision or Temporal rule reads
  `llm_usage`.
- **Staff only. The patient never sees a cost**, a token count or a model: no patient route
  carries one.
  - The Case Monitor (`/staff/monitor`, any staff member) has a strip at the top, "עלות LLM ממוצעת
    לפנייה", with the total, the cases and the calls for a range (the metrics screen's presets: 24
    hours, 7, 30 or 90 days; default 30). It also has an "עלות LLM" column, and the expanded row shows the tokens, the calls, the
    cost and a per-call breakdown, with the Hebrew labels beside the codes.
  - The admin metrics screen (`/staff/metrics`, `admin_staff`) has an "עלות LLM" group.
  - The API is `GET /api/staff/llm-costs`, `CaseSummary.llm_cost_usd`, `CaseDetail.llm_usage`
    and the metrics' `llm` group (`docs/api.md` §10). The cost is not in `ReviewContext`'s hashed
    `shown`, so a new usage row never changes `shown_context_ref`.

## 5. The NULL rule

`docs/api.md` §10 is the contract. In short:

- A case's or a window's cost is **the sum of the non-null costs** of its rows.
- A row is **unpriced** when its model has no price (`price_input_per_mtok IS NULL`). A priced
  API error is not unpriced: it keeps its price, has `NULL` tokens and cost, and billed nothing
  we can see.
- The cost is `null` when there is **no row at all** (the UI shows "—": nothing processed yet), or
  when there are **unpriced rows and no priced cost** (the UI shows "מחיר לא ידוע").
- Rows that are all priced API errors cost `0.00000000`, not `null`.
- A cost next to `unpriced_calls > 0` is a lower bound (the UI marks it partial).
- The averages (`avg_cost_per_case_usd`, `avg_cost_per_completed_case_usd`) divide by the cases
  with at least one priced row, so a case with no known cost does not drag them down.

## 6. Measured tokens per call

These are the **controller's live measurement of 2026-09-27** (design D9). It used `gpt-5.6-luna`
with `reasoning_effort=none`. Each call
kind ran 3 times, and the token counts were identical every run. There were no DB writes and no
patient data. The cost column is computed from those counts at $0.20 / $1.20 per 1M tokens:

| Call kind | Input | Output | Cost per call (USD) |
|---|---:|---:|---:|
| Intent - the request | 283 | 12 | 0.0000710 |
| Intent - with one document | 310 | 12 | 0.0000764 |
| Safety - the request | 291 | 15 | 0.0000762 |
| Safety - with one document | 318 | 15 | 0.0000816 |
| Safety - instructions re-check (retrieved content, §3) | 337 | 15 | 0.0000854 |
| Planner - `plan` | 392 | 54 | 0.0001432 |
| Planner - `propose` (one step) | 395 | 17 | 0.0000994 |
| Response Evaluator | 286 | 14 | 0.0000740 |
| document-service `DocumentClassify` (text PDF) | 612 | 37 | 0.0001668 |
| document-service `DocumentVision` (photo/scan) | 2690 | 30 | 0.0005740 |

- **`DocumentClassify`** is the mean over 6 demo PDFs of 342-550 characters. Input ranged over
  561-656 tokens; the mean is 612/37.
- **`DocumentVision`** is one 1200×1600 JPEG photo.

Example: Planner `plan` = 392 × 0.20 / 1e6 + 54 × 1.20 / 1e6 = 0.0000784 + 0.0000648 = 0.0001432.

## 7. What the three §0 scenarios cost (an estimate)

**These are estimates, not measurements of a case.** Each one is the measured tokens per call
(§6) times the number of calls counted in the scenario. The running system records every real
case's own figures in `llm_usage` and shows them in the Case Monitor.

### Call counts

Counted from the `llm_usage` rows of each golden scenario, run end to end by the Agent
Orchestrator with FakeProvider and a `UsageRecorder`. The test is
`tests/test_llm_costs.py::test_the_golden_scenarios_llm_calls_per_kind`, and it asserts these
counts. The kind of each Intent/Safety/Planner row comes from what the call was sent. The golden
path answers every call usably at the first attempt, so each count is also the number of calls.

| Call kind | Scenario 1 - normal flow, missing document | Scenario 2 - medical escalation | Scenario 3 - technical failure, bounded retry |
|---|---:|---:|---:|
| Intent - the request | 1 | 1 | 1 |
| Safety - the request | 1 | 1 | 1 |
| Intent - with document | 1 | - | 1 |
| Safety - with document | 1 | - | 1 |
| Safety - instructions re-check | 1 | - | 1 |
| Planner - `plan` | 1 | - | 1 |
| Planner - `propose` | 4 | - | 7 |
| Response Evaluator | 1 | - | 1 |
| **Agent calls (rows)** | **11** | **2** | **14** |

Why these counts:

- **Scenario 1.** The request is classified (Intent + Safety), planned, and the Planner proposes
  the four steps (CheckAppointment, CheckDocuments, LoadInstructions, SendStatusUpdate). The
  retrieved instructions are re-checked for safety. The uploaded blood test re-classifies the case
  (T10: Intent + Safety with the document). The status message is evaluated once.
- **Scenario 2.** The MedicalQuestion is found at classification. A human decides, and no further
  LLM call is made.
- **Scenario 3.** As scenario 1, plus three more `propose` calls. CheckDocuments is proposed 3
  times and fails 3 times (the mock's transient failure; RetryExhausted), then once more after the human approval, for 7 in
  all.

### Per scenario and per 1,000 requests

Computed in `Decimal`: Σ count × tokens, Σ count × per-call cost.

| | Scenario 1 | Scenario 2 | Scenario 3 |
|---|---:|---:|---:|
| Input tokens | 3,797 | 574 | 4,982 |
| Output tokens | 205 | 27 | 256 |
| **Cost per request (USD)** | **0.0010054** | **0.0001472** | **0.0013036** |
| **Cost per 1,000 requests (USD)** | **1.0054** | **0.1472** | **1.3036** |

The arithmetic:

- **Scenario 1**
  - Input: 283 + 291 + 310 + 318 + 337 + 392 + 4 × 395 + 286 = 3,797.
  - Output: 12 + 15 + 12 + 15 + 15 + 54 + 4 × 17 + 14 = 205.
  - Cost: 3,797 × 0.20 / 1e6 + 205 × 1.20 / 1e6 = 0.0007594 + 0.0002460 = **0.0010054**.
- **Scenario 2**
  - Input: 283 + 291 = 574.
  - Output: 12 + 15 = 27.
  - Cost: 0.0001148 + 0.0000324 = **0.0001472**.
- **Scenario 3**
  - Input: 3,797 + 3 × 395 = 4,982.
  - Output: 205 + 3 × 17 = 256.
  - Cost: 0.0009964 + 0.0003072 = **0.0013036**.

### The upload on the owner's stack

With `DOCUMENT_SERVICE_URL` set, the patient's upload in scenarios 1 and 3 is a real file, and the
document-service makes one more call for it (`source` `document_service`). In the golden run it
is the text upload, with no call. Add one of:

| Upload | Extra cost | Scenario 1 total | per 1,000 | Scenario 3 total | per 1,000 |
|---|---:|---:|---:|---:|---:|
| Text PDF (`DocumentClassify`, 612/37) | 0.0001668 | 0.0011722 | 1.1722 | 0.0014704 | 1.4704 |
| Photo or scan (`DocumentVision`, 2690/30) | 0.0005740 | 0.0015794 | 1.5794 | 0.0018776 | 1.8776 |

Scenario 2 has no upload.

### What would move these numbers

- **A retried call adds a row.** An unusable answer is retried, up to three in a row. Every
  attempt is a row, so it adds its tokens.
- **More retrieval failures** add one `propose` call each (0.0000994), as scenario 3 shows.
- **Longer text** raises the input tokens: a longer request, a longer instruction text or status
  message, or a longer document.
- **Output dominates less than it looks.** Output is 6 times the price of input, but the answers
  are short (12-54 tokens), so input is about three quarters of every scenario's cost.

## 8. Confidence scores and model cascading

Not built, deliberately (design §1).
- §18.5 fixes one model for the four calls, and §0 runs every scenario on the same model.
- An uncertain case already goes to a human, not to a stronger model: HighRisk/CriticalRisk Safety
  escalates, three unusable answers escalate, and a medical flag from the Response Evaluator holds
  the message for a `ContentApproval`.
- A model's self-reported confidence is not calibrated. Routing on it would let an LLM output steer
  control flow, against "the LLM proposes, deterministic layers decide". A cascade would be its own
  sub-project with a spec change.
