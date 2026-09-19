"""The SLA Worker and restart recovery (spec §3.1 PatientSlaExpired, §12.2; Execution design §5)."""
import logging
import time
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


def test_a_failed_tick_never_logs_patient_data(sm, caplog):
    """spec §12.3: the application log must never contain patient_id. logger.exception's
    traceback (and a SQLAlchemy StatementError's bound parameters) would leak it, so the
    worker logs only the exception type."""
    class FailingWorker(SlaWorker):
        def tick(self) -> list:
            raise RuntimeError("boom for P-10041")

    worker = FailingWorker(sm)
    with caplog.at_level(logging.ERROR, logger="hospital_agent.execution.sla"):
        stop = worker.run_in_background(interval_seconds=0.01)
        try:
            deadline = time.monotonic() + 5
            while not caplog.records and time.monotonic() < deadline:
                time.sleep(0.01)
        finally:
            stop.set()
    assert caplog.records
    for record in caplog.records:
        assert "P-10041" not in record.getMessage()
        assert record.exc_info is None
    assert any("RuntimeError" in record.getMessage() for record in caplog.records)


def test_a_failing_tick_does_not_stop_the_worker(sm):
    """spec §14 fail closed: one bad tick (e.g. ReprocessLimitExceeded, a DB error) must
    not silently end the background loop - a later expired deadline still has to be
    escalated."""
    calls: list[int] = []

    class FailOnceWorker(SlaWorker):
        def tick(self) -> list:
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom")
            return []

    worker = FailOnceWorker(sm)
    stop = worker.run_in_background(interval_seconds=0.01)
    try:
        deadline = time.monotonic() + 5
        while len(calls) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        stop.set()
    assert len(calls) >= 2


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


def test_d28_an_intent_that_never_started_escalates_and_is_never_replayed(sm, app_engine):
    """POLICY_ALLOWED committed the executions 'intent' row, then the process died before
    start_execution: the case is stranded in RetrievingData waiting on a decision no one
    will start. recover() escalates it - no call was ever made, so there is no outcome row -
    and marks the row 'failed' so it is not picked up again."""
    gw = MockGateway()
    d = Driver(sm, app_engine, gateway=gw)
    d.to_classified()
    d.plan()
    d.propose()
    d.allow()
    [result] = recover(sm)
    assert result.committed and gw.calls == []
    assert (d.state, d.case.escalation_kind) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.EXECUTION_UNKNOWN)
    assert execution(d, d.last_execution_id).status == "failed"
    assert [row for row in d.trace() if row.record_type == "ExecutionUnknown"] == []
    assert recover(sm) == []
    d.execute()
    assert gw.calls == []
