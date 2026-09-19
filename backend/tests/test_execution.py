"""The Tool Executor end to end on Postgres, plus the execution D-tests of §16 (Execution design §8)."""
import pytest

from hospital_agent import repository
from hospital_agent.db import executions
from hospital_agent.execution.gateway import OK, MockGateway, ToolResult
from hospital_agent.execution.verify import REVERIFICATION_FAILED
from hospital_agent.naming import EscalationKind, Event, State
from tests.driver import Driver
from tests.test_policy_d_tests import MEDICAL, at_step, content_approval, insert_approval


def execution(d: Driver, execution_id: str):
    with d.engine.connect() as conn:
        return repository.load_execution(conn, execution_id)


def rows(d: Driver, record_type: str) -> list:
    return [row for row in d.trace() if row.record_type == record_type]


def to_step_2(d: Driver) -> None:
    d.to_classified()
    d.plan()
    d.run_step()
    d.advance()


# --- the path of one call (§1, §18.2) --------------------------------------------------------

def test_a_call_writes_the_started_pair_then_its_outcome(sm, app_engine):
    gw = MockGateway()
    d = Driver(sm, app_engine, gateway=gw)
    d.to_classified()
    d.plan()
    d.run_step()
    tail = [(row.record_type, row.event, row.state_after) for row in d.trace()[-4:]]
    assert tail == [
        ("ExecutionStarted", "TOOL_EXECUTION_STARTED", "RetrievingData"),
        ("ExecutionStarted", "AUDIT_RECORDED", "RetrievingData"),
        ("ExecutionSucceeded", "DATA_RETRIEVED", "RetrievingData"),
        ("Transition", "DATA_RETRIEVED", "Planning"),
    ]
    assert gw.calls == [("CheckAppointment", {"patient_id": d.patient_id}, f"{d.case_id}:1:0:1")]
    assert execution(d, d.last_execution_id).status == "succeeded"
    assert d.case.attempt_count == 1 and d.case.appointment_at is not None


def test_a_decision_is_never_executed_twice(sm, app_engine):
    gw = MockGateway()
    d = Driver(sm, app_engine, gateway=gw)
    d.to_classified()
    d.plan()
    d.run_step()
    replay = d.execute()  # the same execution_id again
    assert (replay.committed, replay.reason, d.state) == (False, REVERIFICATION_FAILED, State.PLANNING)
    assert len(gw.calls) == 1
    [blocked] = rows(d, "Blocked")
    assert (blocked.event, blocked.policy_reasons) == ("TOOL_EXECUTION_STARTED", [REVERIFICATION_FAILED])


def test_a_decision_that_drifted_before_the_call_escalates(sm, app_engine):
    gw = MockGateway()
    d = Driver(sm, app_engine, gateway=gw)
    d.to_classified()
    d.plan()
    d.propose()
    d.allow()
    with app_engine.begin() as conn:  # something changed the binding after the decision was accepted
        conn.execute(executions.update()
                     .where(executions.c.execution_id == d.last_execution_id)
                     .values(plan_hash="0" * 64))
    d.execute()
    assert gw.calls == []
    assert (d.state, d.case.escalation_kind) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.EXECUTION_UNKNOWN)


def test_a_result_rejected_by_its_own_guard_escalates(sm, app_engine):
    """valid_tool_result rejects a non-datetime appointment_at (Execution design §5) - fail closed (§14)."""
    class BadAppointmentGateway(MockGateway):
        def call(self, action, parameters, idempotency_key):
            if action == "CheckAppointment":
                self.calls.append((action, dict(parameters), idempotency_key))
                return ToolResult(OK, {"appointment_at": "not-a-datetime"})
            return super().call(action, parameters, idempotency_key)

    gw = BadAppointmentGateway()
    d = Driver(sm, app_engine, gateway=gw)
    d.to_classified()
    d.plan()
    d.run_step()
    assert (d.state, d.case.escalation_kind) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.EXECUTION_UNKNOWN)
    assert len(gw.calls) == 1
    assert execution(d, d.last_execution_id).status != "started"


def test_a_result_field_outside_the_action_is_dropped(sm, app_engine):
    """gateway.RESULT_FIELDS: an action's result may only set its own fields (design §3.4) -
    LoadInstructions cannot make held_documents complete via DATA_RETRIEVED."""
    class LeakyGateway(MockGateway):
        def call(self, action, parameters, idempotency_key):
            result = super().call(action, parameters, idempotency_key)
            if action == "LoadInstructions":
                return ToolResult(result.kind, {**result.data, "held_documents": ["referral", "blood_test"]})
            return result

    d = Driver(sm, app_engine, gateway=LeakyGateway())
    d.to_classified()
    d.plan()
    d.run_step()  # CheckAppointment
    d.advance()
    d.run_step()  # CheckDocuments -> held: referral only, blood_test still missing
    d.advance()
    d.run_step()  # LoadInstructions -> AssessingReadiness (the leaked held_documents is ignored)
    d.assess()
    assert d.state is State.AWAITING_PATIENT_INPUT


def test_a_call_that_raises_escalates_without_retry(sm, app_engine):
    class RaisingGateway(MockGateway):
        def call(self, action, parameters, idempotency_key):
            self.calls.append((action, dict(parameters), idempotency_key))
            raise RuntimeError("boom")

    gw = RaisingGateway()
    d = Driver(sm, app_engine, gateway=gw)
    d.to_classified()
    d.plan()
    d.run_step()
    assert (d.state, d.case.escalation_kind) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.EXECUTION_UNKNOWN)
    assert execution(d, d.last_execution_id).status == "unknown"
    assert len(gw.calls) == 1


# --- retry (§12.1) ---------------------------------------------------------------------------

def test_d7_three_attempts_then_retry_exhausted(sm, app_engine):
    gw = MockGateway(failures={"CheckDocuments": 5})
    d = Driver(sm, app_engine, gateway=gw)
    to_step_2(d)
    for _ in range(3):
        d.run_step()
    events = [row.event for row in d.trace() if row.record_type == "Transition"]
    assert events.count("POLICY_ALLOWED") == 4 and events[-1] == "RETRY_EXHAUSTED"
    assert (d.state, d.case.escalation_kind) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.RETRY_EXHAUSTED)
    assert not d.propose().committed  # no fourth automatic attempt
    assert [call[0] for call in gw.calls].count("CheckDocuments") == 3


def test_d12_every_attempt_is_audited(sm, app_engine):
    d = Driver(sm, app_engine, gateway=MockGateway(failures={"CheckDocuments": 3}))
    to_step_2(d)
    for _ in range(3):
        d.run_step()
    started = [row for row in rows(d, "ExecutionStarted") if row.event == "TOOL_EXECUTION_STARTED"]
    assert [(row.action, row.attempt_number) for row in started[1:]] == [("CheckDocuments", n) for n in (1, 2, 3)]
    assert len(rows(d, "ExecutionFailed")) == 3


def test_d23_a_new_step_starts_with_a_fresh_attempt_budget(sm, app_engine):
    d = Driver(sm, app_engine, gateway=MockGateway(failures={"CheckAppointment": 2}))
    d.to_classified()
    d.plan()
    for _ in range(3):
        d.run_step()
    assert d.case.attempt_count == 3
    d.advance()
    assert (d.case.current_step, d.case.attempt_count) == (2, 0)


@pytest.mark.parametrize("gateway", [
    MockGateway(failures={"CheckDocuments": 1}, non_idempotent=frozenset({"CheckDocuments"})),  # D27
    MockGateway(errors=frozenset({"CheckDocuments"})),                                        # a hard error
])
def test_d27_a_failure_that_cannot_be_retried_goes_to_a_human(sm, app_engine, gateway):
    d = Driver(sm, app_engine, gateway=gateway)
    to_step_2(d)
    d.run_step()
    assert (d.state, d.case.escalation_kind) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.NON_IDEMPOTENT_FAILURE)
    assert [call[0] for call in gateway.calls].count("CheckDocuments") == 1


def test_human_approval_after_retry_exhausted_opens_a_new_cycle(sm, app_engine):
    d = Driver(sm, app_engine, gateway=MockGateway(failures={"CheckDocuments": 3}))
    to_step_2(d)
    for _ in range(3):
        d.run_step()
    d.human(Event.HUMAN_APPROVED, d.approval("approve"))
    assert (d.state, d.case.retry_cycle, d.case.attempt_count) == (State.PLANNING, 1, 0)
    d.run_step()
    assert d.state is State.PLANNING and execution(d, d.last_execution_id).idempotency_key.endswith(":2:1:1")


# --- delivery (§12.5, D11) --------------------------------------------------------------

def test_d11_an_approved_medical_message_is_delivered_once(sm, app_engine):
    gw = MockGateway()
    d = Driver(sm, app_engine, gateway=gw)
    at_step(d, 4)
    request = d.request(outgoing_message=MEDICAL)
    approval_id = insert_approval(app_engine, content_approval(d, request.execution_id))
    d.allow(execution_id=request.execution_id, outgoing_message=MEDICAL, approval_id=approval_id)
    d.execute()
    assert d.state is State.COMPLETED
    assert list(gw.delivered.values()) == [{"patient_id": d.patient_id, "content_hash": MEDICAL.content_hash}]
    with app_engine.connect() as conn:
        assert repository.load_approval(conn, approval_id).consumed_at is not None
