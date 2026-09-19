"""The SLA Worker and restart recovery (spec §3.1 PatientSlaExpired, §12.2; Execution design §5)."""
from datetime import timedelta

from hospital_agent import repository
from hospital_agent.execution.background import start_background
from hospital_agent.execution.gateway import MockGateway
from hospital_agent.execution.recovery import recover
from hospital_agent.execution.sla import SlaWorker
from hospital_agent.naming import Component, EscalationKind, Event, State
from tests.driver import Driver


def execution(d: Driver, execution_id: str):
    with d.engine.connect() as conn:
        return repository.load_execution(conn, execution_id)


def to_started(d: Driver) -> None:
    """A CheckAppointment call has started - and the process dies before its outcome."""
    d.to_classified()
    d.plan()
    d.propose()
    d.allow()
    assert d.sm.start_execution(d.case_id, d.last_execution_id).started


# --- SLA Worker (§3.1 PatientSlaExpired) -----------------------------------------------------

def to_awaiting_patient(d: Driver) -> None:
    d.to_classified()
    d.plan()
    for _ in range(2):
        d.run_step()
        d.advance()
    d.run_step()
    d.assess()
    assert d.state is State.AWAITING_PATIENT_INPUT


def test_sla_worker_escalates_only_after_the_deadline(sm, app_engine):
    d = Driver(sm, app_engine)
    to_awaiting_patient(d)
    deadline = d.case.patient_deadline
    worker = SlaWorker(sm)
    assert worker.tick() == []
    sm.clock = lambda: deadline + timedelta(seconds=1)
    [result] = worker.tick()
    assert result.committed
    assert (d.state, d.case.escalation_kind) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.PATIENT_SLA_EXPIRED)


def test_a_stale_timeout_is_discarded(sm, app_engine):
    d = Driver(sm, app_engine)
    to_awaiting_patient(d)
    registered = d.case.state_version
    d.upload("blood_test")
    stale = sm.apply(d.case_id, Event.TIMEOUT_EXPIRED, {"registered_state_version": registered}, Component.SLA_WORKER)
    assert not stale.committed and d.state is State.CLASSIFYING


# --- restart recovery (§12.2, D28) -----------------------------------------------------------

def test_d28_a_started_execution_without_outcome_escalates_and_is_never_replayed(sm, app_engine):
    gw = MockGateway()
    d = Driver(sm, app_engine, gateway=gw)
    to_started(d)
    [result] = recover(sm)
    assert result.committed and gw.calls == []
    assert (d.state, d.case.escalation_kind) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.EXECUTION_UNKNOWN)
    assert execution(d, d.last_execution_id).status == "unknown"
    assert [row.outcome for row in d.trace() if row.record_type == "ExecutionUnknown"] == ["unknown"]
    assert recover(sm) == []
    d.execute()
    assert gw.calls == []


def test_startup_recovers_before_the_sla_worker_starts(sm, app_engine):
    d = Driver(sm, app_engine)
    to_started(d)
    stop = start_background(sm, interval_seconds=3600)
    try:
        assert d.case.escalation_kind is EscalationKind.EXECUTION_UNKNOWN
    finally:
        stop.set()


def test_an_intent_that_never_started_is_left_alone(sm, app_engine):
    d = Driver(sm, app_engine)
    d.to_classified()
    d.plan()
    d.propose()
    d.allow()
    assert recover(sm) == [] and execution(d, d.last_execution_id).status == "intent"
    assert d.state is State.RETRIEVING_DATA
