# Alloy - the §3 state machine, checked

The other formal tools check a decision (OPA, Prolog, Datalog), a deadline (Z3 §9.1), the layers'
agreement (Z3 §9.2) or a trace as it happens (the Temporal Monitor). Alloy checks the **state
machine itself, over every trace**: that no case can get stuck, that only staff take a case out
of review, that a medical question never returns to automation, and so on - for any outcome of
the guards.

| File | What | Written by |
|---|---|---|
| `fsm_model.als` | The 44 rows of `fsm.py` (41 of §3 + 3 of sub-project 15) as Alloy 6 facts, and the trace semantics | **Generated** by `python -m hospital_agent.policy.alloy_model`; `backend/tests/test_alloy_model.py` fails if it drifts from `fsm.py` |
| `fsm_checks.als` | The properties of the table alone (A, B) and the reachability of every State (D) | Hand |
| `fsm_plan.als` | A refinement: the plan pointer (steps 1-4) and the six guards that read it, as `guards.py` writes them. The two properties that hold only thanks to those guards (P2, P3), the bug Alloy found replayed on the old guard (P1, row 99), and the scenario traces (E1-E3) | Hand - must follow `guards.py` |
| `RESULTS.md` | The last run: every command, its result, three scenario traces and a state diagram | **Generated** by `scripts/alloy_check.py` |
| `out/` | Alloy's raw output (`receipt.json`, one JSON per solution) | Alloy - not committed |

## Running it

Alloy 6.2.0 is one JAR, not committed. Put it in `tools/` once (about 21 MB, from the official
AlloyTools release):

```bash
curl -L -o tools/org.alloytools.alloy.dist.jar https://github.com/AlloyTools/org.alloytools.alloy/releases/download/v6.2.0/org.alloytools.alloy.dist.jar
```

Then, with Java 17+ on the host:

```bash
python scripts/alloy_check.py
```

It runs every command, writes `RESULTS.md`, and exits non-zero if any result differs from the
expected one. After a change to `fsm.py`, regenerate the model first:

```bash
docker compose run --rm backend python -m hospital_agent.policy.alloy_model > docs/alloy/fsm_model.als
```

To explore an instance by hand, open `fsm_checks.als` in the Alloy GUI
(`java -jar tools/org.alloytools.alloy.dist.jar gui`).

## What is modelled, and what is not

- **Modelled:** the State, the escalation kind a case carries, and which row fired. An
  escalation row records one of its kinds; the reply cycle keeps the kind; every other row
  clears it. A `HUMAN_APPROVED` row fires only for the kind it requires.
- **Guards:** in `fsm_model.als` every guard is "may hold", so a property proven there (A, B)
  holds for any guard outcome. Two properties do not hold on the table alone - "nothing is
  sent before Ready" and "no planning without a plan" - and are checked in `fsm_plan.als`
  against the guards that protect them (`delivery_action`, `DeliveryStepPending`,
  `ReadinessInProgress`, `CanAdvance`, ...), modelled as `guards.py` writes them.
- **The bug it found (row 99):** C1's counterexample led to a real gap - `STEP_ADVANCED` could
  walk a plan into the delivery step, and a case reached `Delivering` with no data retrieved and
  no readiness check. Confirmed against the real State Manager, fixed in `can_advance`, and
  replayed in `fsm_plan.als`: P1 (old guard) finds it, P2 (new guard) holds. P1 is the one
  expected counterexample in the report - the evidence - and the script fails if it disappears.
- **The empty-set trap.** A `lone` field (the State before the first step, the row that fired on
  a stutter) is empty, and in Alloy the empty set is `in` every set - so `x in S` is true when
  `x` is empty. Every membership here is written `some (x & S)` / `no (x & S)`. This trap made
  the first runs report false counterexamples, and once hid every completion in `fsm_plan.als`;
  a scenario that must stay reachable (now E1) is what caught the second.
- **Bounded:** Alloy checks every trace up to the step bound (20). The longest scenario path is
  10 steps, so the bound covers each scenario twice over; it is not a proof for unbounded traces.
- **One case at a time.** Cases do not interact in the state machine.
