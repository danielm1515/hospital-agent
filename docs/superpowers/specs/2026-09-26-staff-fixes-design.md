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
5. Follow-up diagnosis found decision 1's log lines were silently dropped in practice: Uvicorn configures only
   its own loggers, and the root logger stays at its default `WARNING`, so every `hospital_agent.*`
   `logger.info(...)` - not just `llm/telemetry.py`'s, existing ones too (e.g. `session.py`'s `"pdf reply:
   unreadable"`) - never reached anywhere. `hospital_agent/logging_setup.py`'s `configure()` now sets the
   `hospital_agent` logger to `INFO`, adds one `StreamHandler` to stderr in the documented format (time,
   level, logger name, message), and sets `propagate=False` so nothing is logged twice; it is idempotent (a
   second call adds no second handler) and is called only from `api/app.py`'s owned-app path (a real server),
   never a test's - a test always injects an engine, and pytest's `caplog` is unaffected either way (verified:
   the two `test_app_orchestrator.py` tests that do exercise the real owned path, and every `caplog` test in
   the suite, all still pass).

**Deviations from the text above.** `is_retryable()`'s `NotFoundError` covers "the model" case from decision
2's diagnosis paragraph, plus the codebase's existing (pre-existing, unrelated to this task) schema-failure
code is spelled `schema_violation`, not this document's `schema_invalid` - kept as `schema_violation` to avoid
touching unrelated, already-passing tests; `is_retryable()` treats it as retryable either way, since it is not
an `api:` code. `LLMUnusable.retryable` (decision 2) is derived automatically from the reason string by
`is_retryable()` rather than requiring every `raise LLMUnusable(...)` site to pass `retryable=False`
explicitly, so every existing raise site needed no change; an explicit `retryable=` kwarg is still honoured
where a caller wants to override it.

## Task 2 - "לא הצלחנו לקרוא את המסמך" on a patient's upload

**Diagnosis** (`.superpowers/sdd/2026-09-26-staff-fixes/diagnosis-2-7.md`, live logs, read-only). The two uploads
the document-service ever received were readable text PDFs; its own OpenAI call got `429` (the same empty
account) and `intake.py:96-99` turned every `ClassifierFailed` - an API failure included - into
`DOCUMENT_UNREADABLE`, which hospital-agent shows as "unreadable, check it is a clear PDF". The reason was
thrown away. `DOCUMENT_UNREADABLE` stands for eight different causes. There is no OCR; a scanned PDF (no text
layer) and every image are refused.

**Decisions.**
1. document-service: a provider failure (`api:*`) is `503 classifier_unavailable` (the `storage_unavailable`
   pattern), never a verdict on the file; hospital-agent already turns a 503 into "שירות המסמכים אינו זמין".
2. document-service: every refusal carries a fixed `reason` code (`not_supported_format`, `too_large`,
   `parse_error`, `no_text_layer`, `too_many_pages`, `too_much_text`, `classifier_unparsable`, `unknown_type`,
   `future_date`, `no_date`, `too_old`), in its log line and, additively, in the 201 body. Never content.
3. Scans and images: an LLM **vision** call inside the existing classifier (same prompt contract, same schema,
   one call), not tesseract - it fits the one-call structure and the 70 s budget, needs no system packages,
   and tesseract's Hebrew on RTL lab tables loses exactly the date and type validity depends on. JPEG and PNG
   are accepted by magic bytes; a PDF without a text layer sends its first pages' embedded images (at most 4,
   downscaled). The image goes to the same processor the text already goes to (same data class). If the
   model refuses images, the answer is `classifier_unavailable`/`unreadable` - fail closed; `OPENAI_MODEL`
   can point at a vision model.
4. hospital-agent: the picker and the intake client accept PDF, JPEG and PNG (real `Content-Type` by magic
   bytes); the optional `reason` becomes finer patient codes (`unrecognised_type`, `unreadable_scan`,
   `bad_date`, `too_old`, `no_date`) with a Hebrew sentence each that says what happened and what to do; the
   client's own refusals say it too ("הקובץ גדול מ־10MB. העלו קובץ קטן יותר."). The real reason is logged
   as a code (the logging of Task 1 now reaches stderr). A document that then raises the safety level and
   escalates (T10) is correct behaviour, not part of this fix.
