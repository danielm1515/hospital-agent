"""The three scenarios of §0, checked against the golden traces of §15.

Policy decisions (OPA + Prolog), the Readiness Check (Z3) and the Temporal Monitor
are the real ones; tests/driver.py plays only the components not built yet.

The Core has no Tool Executor yet, so TOOL_EXECUTION_STARTED / AUDIT_RECORDED rows
are filtered out of the golden trace; the audit row totals (35, 4, 54) are checked
in sub-project 3. Every other (state after, event) pair must match exactly.
"""
from datetime import UTC, datetime, timedelta

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
    d.retrieve_step(appointment_at=datetime.now(UTC) + timedelta(hours=96))  # CheckAppointment
    d.advance()
    d.retrieve_step(required_documents=["referral", "blood_test"], held_documents=["referral"])
    d.advance()
    d.retrieve_step()                                   # LoadInstructions -> AssessingReadiness
    d.assess()                                          # blood_test missing, Z3 unsat: safe to ask
    d.upload("blood_test")
    d.classify()                                        # re-classified, readiness in progress
    d.assess()                                          # everything held -> READINESS_PASSED
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
    """Each attempt is started through the State Manager, which counts it (attempt_count)
    and writes the ExecutionStarted pair; the Tool Executor that also makes the call and
    records its outcome replaces this in obs.golden."""
    d = Driver(sm, app_engine)
    d.to_classified()
    d.plan()
    d.retrieve_step(appointment_at=datetime.now(UTC) + timedelta(hours=96))  # CheckAppointment
    d.advance()
    for _ in range(2):                                  # attempts 1 and 2 time out
        d.propose()
        d.allow()
        d.sm.start_execution(d.case_id, d.last_execution_id)
        d.transient_failure()
    d.propose()
    d.allow()
    d.sm.start_execution(d.case_id, d.last_execution_id)
    d.retry_exhausted()                                 # attempt 3 -> a human decides
    d.human(Event.HUMAN_APPROVED, d.approval("approve"))
    assert (d.case.retry_cycle, d.case.attempt_count) == (1, 0)
    d.retrieve_step(required_documents=["referral", "blood_test"], held_documents=["referral"])
    d.advance()
    d.retrieve_step()
    d.assess()
    d.upload("blood_test")
    d.classify()
    d.assess()
    d.plan_delivery()
    d.propose()
    d.allow()
    d.deliver()

    assert transitions(d) == expected(3)
    assert d.state is State.COMPLETED
