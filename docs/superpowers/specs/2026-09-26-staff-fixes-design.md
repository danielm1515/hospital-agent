# Sub-project 17: seven fixes from the owner's review (2026-09-26)

Autonomous mode (CLAUDE.md), with the owner's three rulings of 2026-09-26: the API key is reported only as
present + length (never a character of it); each task is diagnosed and reported, then fixed without waiting;
work starts after sub-project 16's merge. Hard rules from the owner: no safety mechanism is weakened to reduce
escalations (OPA, guards, T1-T13, Z3 thresholds, classifier prompts); no secret or patient text in a log;
State names stay exactly as in the transition table. One commit (or a small commit set) per task, each with a
test for what it fixes.

## Task 1 - is the LLM called at all?

**Diagnosis (live, read-only).** The key is loaded into the backend (present, 164 characters; no org/project
override, no base URL), the model is `gpt-5.6-luna`, and there is no runtime mock (`FakeProvider` exists only
for tests and `obs.golden`; `select_provider()` returns None without a key and the Orchestrator then does not
start). The calls do leave the server: one diagnostic Intent call returned `429 RateLimitError`,
`code=credit_balance_exhausted`, `type=insufficient_quota` - the account had no credit, and a refused call is
not billed, so the dashboard showed nothing. Every failure became `LLMUnusable("api:RateLimitError")`, was
retried three times and escalated as `ClassificationFailed` without a single log line naming the cause. Open
escalations at the time: `ClassificationFailed` from `Classifying` 6, `PatientSlaExpired` from
`AwaitingPatientInput` 2.

**Decisions.**
1. Every attempt of every LLM call is logged in the parent process, one line:
   `llm call=<intent|safety|planner|evaluator> model=<m> ms=<n> outcome=<ok|code>` where the code is
   `api:<ExceptionType>[:<api code>]`, `unparsable` or `schema_invalid`. No request text, prompt or answer.
   The Response Evaluator's attempts are logged by its parent-side loop (its worker process has no log config).
2. An error that no retry can fix - `AuthenticationError`, `PermissionDeniedError`, `NotFoundError` (the
   model), and a 429 whose code is `insufficient_quota` / `credit_balance_exhausted` - is not retried: `ask()`
   and the Evaluator raise `LLMFailed` at once. The escalation is exactly the same as after three failures
   (same event, same kind); only the two pointless calls are saved.
3. The last outcome is kept in memory (`llm/telemetry.py`: the last error code and time, the last success
   time) and shown: `/health` gains `llm`, and a staff-only `GET /api/staff/system-status` returns the
   Orchestrator status and the LLM's last error, which the staff screens show as a banner (Hebrew label
   beside the code) while the last call failed or the Orchestrator is not running.
4. A missing key stays fail-closed as today (the server runs so patients can still submit; the Orchestrator
   does not start and every request waits in `Received`), and is now loud: an ERROR log line at startup and
   the same staff banner. The whole server is not made to crash, which would take the patient screen down
   too. There is no mock to switch on or show.
