# Alloy - the §3 state machine, checked

The other formal tools check a decision (OPA, Prolog, Datalog), a deadline (Z3 §9.1), the layers'
agreement (Z3 §9.2) or a trace as it happens (the Temporal Monitor). Alloy checks the **state
machine itself, over every trace**: that no case can get stuck, that only staff take a case out
of review, that a medical question never returns to automation, and so on - for any outcome of
the guards.

| File | What | Written by |
|---|---|---|
| `fsm_model.als` | The 44 rows of `fsm.py` (41 of §3 + 3 of sub-project 15) as Alloy 6 facts, and the trace semantics | **Generated** by `python -m hospital_agent.policy.alloy_model`; `backend/tests/test_alloy_model.py` fails if it drifts from `fsm.py` |
| `fsm_checks.als` | The properties (`check`) and the reachability / scenario searches (`run`) | Hand |
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
- **Not modelled:** the guards' values. Every guard is "may hold", so a property that Alloy
  proves holds for any guard outcome. A property that needs a guard states it as a named
  assumption - see C1/C2 in `RESULTS.md`, where Alloy shows that "an automatic completion went
  through Ready" fails without the `delivery_action` guard and holds with it. C3/C4 are the same
  shape, and Alloy found that one on its own: "no planning without a complete plan" fails
  without `ReadinessInProgress` (the re-classification row R05) and holds with it.
- **Bounded:** Alloy checks every trace up to the step bound (20). The longest scenario path is
  10 steps, so the bound covers each scenario twice over; it is not a proof for unbounded traces.
- **One case at a time.** Cases do not interact in the state machine.
