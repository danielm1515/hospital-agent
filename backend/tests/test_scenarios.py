"""The three scenarios of §0, checked against the golden traces of §15.

Policy decisions (OPA + Prolog), the Readiness Check (Z3) and the Temporal Monitor
are the real ones; tests/driver.py plays only the components not built yet.

The Core has no Tool Executor yet, so TOOL_EXECUTION_STARTED / AUDIT_RECORDED rows
are filtered out of the golden trace; the audit row totals (35, 4, 54) are checked
in sub-project 3. Every other (state after, event) pair must match exactly.
"""
from hospital_agent.naming import NON_TRANSITION_EVENTS, Event, State
from tests.driver import Driver
from tests.spec_tables import golden_traces


def expected(scenario: int) -> list[tuple[str, str]]:
    skip = {e.value for e in NON_TRANSITION_EVENTS}
    return [(state, event) for state, event in golden_traces()[scenario] if event not in skip]


def transitions(d: Driver) -> list[tuple[str, str]]:
    return [(e.state_after, e.event) for e in d.trace() if e.record_type == "Transition"]


def test_scenario_1_normal_flow(sm, app_engine):
    d = Driver(sm, app_engine)
    d.to_classified()
    d.plan()
    d.retrieve_step()                                   # CheckAppointment
    d.advance()
    d.retrieve_step(required_documents=["referral", "blood_test"], held_documents=["referral"])
    d.advance()
    d.retrieve_step()                                   # LoadInstructions -> AssessingReadiness
    d.assess(96)                                        # blood_test missing, Z3 unsat: safe to ask
    d.upload("blood_test")
    d.classify()                                        # re-classified, readiness in progress
    d.assess(96)                                        # everything held -> READINESS_PASSED
    d.plan_delivery()
    d.propose()
    d.allow()
    d.deliver()

    assert transitions(d) == expected(1)
    assert d.state is State.COMPLETED
    assert [e.record_type for e in d.trace()].count("Blocked") == 0


def test_scenario_2_medical_escalation(sm, app_engine):
    d = Driver(sm, app_engine)
    d.submit()
    d.validate()
    d.medical_question()
    d.human(Event.HUMAN_RESOLVED_CASE, d.approval("resolve"))

    assert transitions(d) == expected(2)
    assert d.state is State.COMPLETED
    assert len(d.trace()) == 4  # §15: "audit rows: 4" - no tool calls in this scenario
    assert d.case.plan_hash is None  # stopped before any plan was built


def test_scenario_3_technical_failure(sm, app_engine):
    """F5: nothing in the Core increments attempt_count (TOOL_EXECUTION_STARTED is refused

    by design, Refinement 3), so AttemptsAvailable always holds here and this scenario's
    retries drive RETRY_EXHAUSTED directly rather than exercising the attempt budget; the
    State Manager entry point that writes ExecutionStarted/AUDIT_RECORDED and increments
    attempt_count in one transaction arrives in sub-project 3.
    """
    d = Driver(sm, app_engine)
    d.to_classified()
    d.plan()
    d.retrieve_step()                                   # CheckAppointment
    d.advance()
    for _ in range(2):                                  # attempts 1 and 2 time out
        d.propose()
        d.allow()
        d.transient_failure()
    d.propose()
    d.allow()
    d.retry_exhausted()                                 # attempt 3 -> a human decides
    d.human(Event.HUMAN_APPROVED, d.approval("approve"))
    assert (d.case.retry_cycle, d.case.attempt_count) == (1, 0)
    d.retrieve_step(required_documents=["referral", "blood_test"], held_documents=["referral"])
    d.advance()
    d.retrieve_step()
    d.assess(96)
    d.upload("blood_test")
    d.classify()
    d.assess(96)
    d.plan_delivery()
    d.propose()
    d.allow()
    d.deliver()

    assert transitions(d) == expected(3)
    assert d.state is State.COMPLETED
