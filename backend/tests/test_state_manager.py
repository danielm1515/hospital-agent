"""State Manager: one transaction per event, Blocked rows, fail-closed behaviour (design §7)."""
import pytest
from sqlalchemy import create_engine

from hospital_agent import repository
from hospital_agent.naming import Component, EscalationKind, Event, State
from hospital_agent.state_manager import (
    CaseNotFound,
    NonTransitionEvent,
    ReprocessLimitExceeded,
    StateManager,
)
from tests.driver import Driver
from tests.fakes import AllowAllMonitor, UnavailableMonitor, ViolatingMonitor, fake_ports


def test_request_submitted_creates_the_case_in_received(sm, app_engine):
    d = Driver(sm, app_engine)
    result = d.submit()
    assert result.committed and result.case_id.startswith("CASE-")
    case = d.case
    assert (case.state, case.state_version, case.patient_id) == (State.RECEIVED, 1, "P-10041")
    [entry] = d.trace()
    assert (entry.record_type, entry.event, entry.state_before, entry.state_after) == (
        "Transition", "REQUEST_SUBMITTED", None, "Received")
    assert entry.guards == {"PatientIdentified": True}


def test_request_without_patient_is_rejected_and_creates_nothing(sm, app_engine):
    result = sm.apply(None, Event.REQUEST_SUBMITTED, {"patient_id": ""})
    assert (result.committed, result.case_id, result.reason) == (False, None, "guard_failed")
    with app_engine.connect() as conn:
        assert repository.list_cases(conn) == []


def test_each_transition_bumps_the_version_and_writes_one_audit_row(sm, app_engine):
    d = Driver(sm, app_engine)
    d.to_classified()
    assert (d.state, d.case.state_version) == (State.CLASSIFIED, 3)
    assert [(e.event, e.state_after) for e in d.trace()] == [
        ("REQUEST_SUBMITTED", "Received"),
        ("REQUEST_VALIDATED", "Classifying"),
        ("INTENT_CLASSIFIED", "Classified"),
    ]
    assert d.case.identity_verified is True


def test_guard_failure_writes_a_blocked_row_and_changes_nothing(sm, app_engine):
    d = Driver(sm, app_engine)
    d.submit()
    result = sm.apply(d.case_id, Event.REQUEST_VALIDATED, {"text": "", "identity_verified": True}, Component.SESSION_SERVICE)
    assert (result.committed, result.reason, result.state_after) == (False, "guard_failed", State.RECEIVED)
    assert d.case.state_version == 1
    blocked = d.trace()[-1]
    assert (blocked.record_type, blocked.event, blocked.policy_reasons) == ("Blocked", "REQUEST_VALIDATED", ["guard_failed"])


def test_system_owned_event_from_the_wrong_source_is_blocked(sm, app_engine):
    d = Driver(sm, app_engine)
    d.submit()
    result = sm.apply(d.case_id, Event.REQUEST_VALIDATED, {"text": "x", "identity_verified": True}, Component.EXTERNAL)
    assert (result.committed, result.reason) == (False, "system_owned_event")
    assert d.state is State.RECEIVED


def test_string_event_names_are_accepted(sm, app_engine):
    d = Driver(sm, app_engine)
    d.submit()
    result = sm.apply(d.case_id, "REQUEST_VALIDATED", {"text": "x", "identity_verified": True}, Component.SESSION_SERVICE)
    assert result.state_after is State.CLASSIFYING


def test_non_transition_events_are_refused(sm, app_engine):
    d = Driver(sm, app_engine)
    d.submit()
    with pytest.raises(NonTransitionEvent):
        sm.apply(d.case_id, Event.TOOL_EXECUTION_STARTED, {}, Component.TOOL_EXECUTOR)


def test_unknown_case_raises(sm):
    with pytest.raises(CaseNotFound):
        sm.apply("CASE-NOPE", Event.REQUEST_VALIDATED, {}, Component.SESSION_SERVICE)


def test_audit_write_failure_rolls_the_transition_back(sm, app_engine, monkeypatch):
    d = Driver(sm, app_engine)
    d.submit()

    def broken_insert(conn, entry):
        raise RuntimeError("disk full")

    monkeypatch.setattr(repository, "insert_audit", broken_insert)
    with pytest.raises(RuntimeError, match="disk full"):
        d.validate()
    monkeypatch.undo()
    assert (d.state, d.case.state_version, len(d.trace())) == (State.RECEIVED, 1, 1)


def test_unavailable_temporal_monitor_means_no_commit(app_engine):
    d = Driver(StateManager(app_engine, AllowAllMonitor(), fake_ports()), app_engine)
    d.submit()
    d.sm = StateManager(app_engine, UnavailableMonitor(), fake_ports())
    with pytest.raises(RuntimeError, match="unavailable"):
        d.validate()
    assert (d.state, len(d.trace())) == (State.RECEIVED, 1)


def test_temporal_violation_blocks_and_escalates(app_engine):
    sm = StateManager(app_engine, ViolatingMonitor(event="PLAN_CREATED", rule="T2"), fake_ports())
    d = Driver(sm, app_engine)
    d.to_classified()
    result = d.plan()
    assert (result.committed, result.reason, result.temporal_violation) == (False, "temporal_violation:T2", "T2")
    assert result.escalation.committed
    case = d.case
    assert (case.state, case.escalation_kind, case.escalated_from_state) == (
        State.AWAITING_HUMAN_REVIEW, EscalationKind.TEMPORAL_VIOLATION, State.CLASSIFIED)
    assert [e.record_type for e in d.trace()][-2:] == ["Blocked", "Transition"]


def test_stale_version_is_reprocessed_then_fails_closed(sm, app_engine, monkeypatch):
    d = Driver(sm, app_engine)
    d.submit()
    calls = []

    def always_stale(conn, case, expected_version):
        calls.append(expected_version)
        return 0

    monkeypatch.setattr(repository, "update_case", always_stale)
    with pytest.raises(ReprocessLimitExceeded):
        d.validate()
    monkeypatch.undo()
    assert len(calls) == 4  # the first attempt + MAX_REPROCESS (3)
    assert (d.state, len(d.trace())) == (State.RECEIVED, 1)


def test_state_survives_a_restart(sm, app_engine):
    """Design §3: the cases row is the only State. A new process continues from it."""
    d = Driver(sm, app_engine)
    d.to_classified()
    d.plan()
    case_id = d.case_id
    app_engine.dispose()

    fresh_engine = create_engine(app_engine.url)
    try:
        restarted = Driver(StateManager(fresh_engine, AllowAllMonitor(), fake_ports()), fresh_engine)
        restarted.case_id = case_id
        assert (restarted.state, restarted.case.current_step) == (State.PLANNING, 1)
        assert restarted.propose().committed
    finally:
        fresh_engine.dispose()
