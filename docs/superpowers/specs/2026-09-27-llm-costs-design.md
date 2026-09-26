# Sub-project 19: LLM token usage and cost per case

Status: design, autonomous mode (CLAUDE.md). The owner approved the proposal on 2026-09-26 ("אוקי") and asked
for the safe, recommended option at every open point ("תלך על הבטוח").

## 1. The request and the gaps

The owner wants every LLM call's cost computed and documented, and the Monitor to show the average cost of a
case. The model is `gpt-5.6-luna`: **$0.20 per 1M input tokens, $1.20 per 1M output tokens** (the owner's
figures). The document-service uses the same model (its `.env` and running container: `OPENAI_MODEL=gpt-5.6-luna`),
for one text-classify or one vision call per upload.

Today nothing is measured. `OpenAIProvider.complete` discards `response.usage`; `telemetry.record` logs only
call, model, time and outcome, with no case; the document-service never reads `usage` either.

**Confidence and model cascading (the owner's question): not built, deliberately.** §18.5 fixes one model for
the four calls and §0 runs every scenario on the same model. An uncertain case already goes to a human, not to a
stronger model: HighRisk/CriticalRisk Safety escalates, three schema failures escalate, and a medical flag from the
Evaluator holds the message for a `ContentApproval`. A model's self-reported confidence is not calibrated, and
routing on it would let an LLM output steer control flow ("the LLM proposes, deterministic layers decide"). A
cascade would be its own sub-project with a spec change; it is out of scope here.

## 2. Binding constraints

- No safety mechanism changes: OPA, guards, T1-T13, Z3, prompts, schemas, the classifier, the Evaluator's
  separate process. `docs/spec/`, the 41 §3 rows, `policy.rego`, `rules.pl`, `flows.dl` byte-identical. Golden
  traces 35/4/54 (they count audit rows only; a separate table adds none).
- §12.3: usage rows carry no request text, prompt, answer or patient field - counts, codes and money only. The
  Application Log line stays code-only.
- Accounting never changes a case's path: a failed usage write is logged (`llm_usage_write_failed`, code only)
  and the case continues. This is bookkeeping, not a safety control - the one place where "fail closed" does not
  apply, recorded in `docs/spec_corrections.md` row 94.
- The patient never sees a cost. Staff see it.

## 3. Decisions

| # | Decision |
|---|---|
| D1 | **Capture.** A frozen `LLMUsage(input_tokens, cached_input_tokens, output_tokens)` read from `response.usage` (`prompt_tokens`, `prompt_tokens_details.cached_tokens`, `completion_tokens`; a missing or malformed `usage` is `None`, never a guess). `OpenAIProvider` gains `complete_with_usage()` returning `(result, usage)`; `complete()` stays for the Protocol and every fake. `LLMUnusable` carries the usage of an answer that arrived but was rejected (unparsable, schema-invalid) - those tokens were billed. An API error carries none. |
| D2 | **Attribution.** A `contextvars` usage scope (`llm/usage.py`: `usage_scope(case_id, recorder)`) set by the Orchestrator around each `run_case`. `ask()` reports every attempt (ok or failed, with its usage) to the scope; the Classifier's `ThreadPoolExecutor` submits through `contextvars.copy_context().run`; the Evaluator's worker returns `(flag, usage)` (and a rejected answer's usage rides on the pickled `LLMUnusable`), and the parent reports it. No scope (D33 eval, golden, tests) means nothing is recorded. |
| D3 | **Pricing** (`llm/pricing.py`): a per-model table, per 1M tokens, `Decimal`: `gpt-5.6-luna` → input 0.20, output 1.20. `LLM_PRICE_INPUT_PER_MTOK` / `LLM_PRICE_OUTPUT_PER_MTOK` in `.env` override the price of the configured model (bad values refuse the override and keep the table, logged). An unknown model → cost `NULL`, shown "מחיר לא ידוע" - never guessed. Cached input tokens are billed at the full input price (the owner gave two prices; this is the upper bound) and stored separately for later. `cost = input × in/1e6 + output × out/1e6`, rounded to 8 decimals. |
| D4 | **Storage:** migration 0008 table `llm_usage`: `usage_id` bigint identity, `case_id` text FK `cases`, `source` (`agent` \| `document_service`), `call` (`Intent`, `Safety`, `Planner`, `Evaluator`, `DocumentClassify`, `DocumentVision`), `model`, `outcome` (`ok` or the telemetry code), the three token counts (non-negative ints, nullable when no usage), `price_input_per_mtok` / `price_output_per_mtok` / `cost_usd` (`Numeric`, nullable), `created_at`. `hospital_app` gets `SELECT, INSERT` only (append-only, like `audit_log`); `hospital_reader` gets nothing. Written in its own short transaction, never inside `StateManager.apply`. |
| D5 | **The document-service** reads `usage` from its one OpenAI call per upload and adds `llm_usage: {call: "classify" \| "vision", model, input_tokens, cached_input_tokens, output_tokens}` to every 201 answer that made a call (`null` otherwise; a duplicate or an early rejection makes none). hospital-agent's `map_answer` validates it strictly (bounded non-negative ints, a model-name pattern) and ignores an invalid one (logged `llm_usage_invalid`) - bookkeeping never rejects an upload. `upload_pdf` / `reply_pdf` record it under the case with `source=document_service`. A 503 `classifier_unavailable` carries no usage (an API error bills nothing to report). |
| D6 | **API** (`docs/api.md`): `CaseSummary.llm_cost_usd` (the case's total, `null` when nothing priced) on the paginated staff list, one grouped query per page; `CaseDetail.llm_usage {calls, input_tokens, cached_input_tokens, output_tokens, cost_usd, unpriced_calls, by_call[]}` on `GET /api/staff/cases/{id}` - **not** in `ReviewContext`'s hashed `shown` (it would change `shown_context_ref` and refuse decisions with `context_changed`); new `GET /api/staff/llm-costs?from=&to=` (any staff; the cohort = cases opened in the window, all their usage; at most 90 days; `422 invalid_range`, `range_too_large`): `{window, cases, cases_with_usage, calls, input_tokens, cached_input_tokens, output_tokens, total_cost_usd, avg_cost_per_case_usd, avg_cost_per_completed_case_usd, unpriced_calls, by_call[], by_source[]}`; the admin `GET /api/admin/metrics` gains the same numbers as an `llm` group inside its REPEATABLE READ snapshot. Money is a decimal string in JSON (no float drift). |
| D7 | **UI (staff only):** the Case Monitor gets a summary strip at the top - "עלות LLM ממוצעת לפנייה" with total, cases and calls for a range (default last 30 days, the same range control style as the metrics screen) - a "עלות LLM" column, and a fact group in the expanded row (tokens, calls, cost, a per-call breakdown; labels beside codes). The metrics screen gets an "עלות LLM" group. Dollars with 4 decimals ("$0.0021"); `null` → "מחיר לא ידוע". |
| D8 | **Docs:** `docs/llm-costs.md` (prices, formula, what is counted and what is not, per-call token measurements, the per-scenario estimate); `docs/api.md`; CLAUDE.md; `docs/spec_corrections.md` row 94. |
| D9 | **Measurement** after the merge: the controller measures real tokens per call type against the live model (no DB writes, no patient data) and multiplies by the call counts of the three §0 scenarios; the running system then records real cases. Reported in `docs/llm-costs.md`. |

## 4. Out of scope

Confidence scoring and model cascading (§1); budgets, alerts or blocking on cost; cost for the D33 evaluation
runs; exact cached-token discounts.
