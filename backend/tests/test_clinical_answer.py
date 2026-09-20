"""Sub-project 8: a clinical answer authorised by a ContentApproval (§5, §12.4, §6.3)."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from hospital_agent import repository
from hospital_agent.case import ApprovalRecord, ExecutionRecord
from hospital_agent.naming import Action, Component, EscalationKind, Event, State
from tests.driver import Driver

ANSWER = "אין להפסיק מדלל דם ללא הנחיית הרופא המטפל."
ANSWER_HASH = "0f2a" * 16


QUESTION = "Should I stop taking my blood thinner?"


def escalated(sm, app_engine) -> Driver:
    """A case sitting in AwaitingHumanReview with escalation_kind=MedicalQuestion.

    The same three driver calls `tests/test_human_review.py` and `tests/test_api_staff.py`
    already use for this shape.
    """
    d = Driver(sm, app_engine)
    d.submit()
    d.validate(QUESTION)
    d.medical_question()
    assert (d.state, d.case.escalation_kind) == (State.AWAITING_HUMAN_REVIEW, EscalationKind.MEDICAL_QUESTION)
    return d


def write_execution(app_engine, d: Driver, *, content_hash: str = ANSWER_HASH) -> str:
    execution_id = f"EXEC-{d.case_id[-8:]}"
    with app_engine.begin() as conn:
        repository.insert_execution(conn, ExecutionRecord(
            execution_id=execution_id, case_id=d.case_id, patient_id=d.patient_id,
            action=Action.ANSWER_CLINICAL_QUESTION.value, step=0, retry_cycle=0, attempt_number=0,
            idempotency_key=f"{d.case_id}:answer", status="succeeded",
            content_hash=content_hash, medical_content_flag=True))
    return execution_id


def write_approvals(
    app_engine, d: Driver, base_execution_id: str, *, workflow_decision: str = "resolve", **changes
) -> tuple[str, str]:
    """A WorkflowDecision for the transition and a ContentApproval for the text.

    `base_execution_id` (not `execution_id`) so a parametrised `changes={"execution_id": None}`
    can override the ContentApproval's own field without colliding with this function's own
    parameter of the same name.
    """
    now = datetime.now(UTC)
    workflow = ApprovalRecord(
        approval_id=f"APPR-W-{d.case_id[-6:]}", approval_type="WorkflowDecision", case_id=d.case_id,
        patient_id=d.patient_id, reviewer_id="coordinator_nurse", reviewer_role="clinical_staff",
        decision=workflow_decision, reason="נענתה על ידי אחות מתאמת", shown_context_ref="ctx-1",
        granted_at=now, valid_until=now + timedelta(hours=1),
        escalation_kind=EscalationKind.MEDICAL_QUESTION.value)
    content = replace(
        workflow, approval_id=f"APPR-C-{d.case_id[-6:]}", approval_type="ContentApproval",
        execution_id=base_execution_id, action=Action.ANSWER_CLINICAL_QUESTION.value,
        content_hash=ANSWER_HASH, escalation_kind=None)
    content = replace(content, **changes)
    with app_engine.begin() as conn:
        repository.insert_approval(conn, workflow)
        repository.insert_approval(conn, content)
    return workflow.approval_id, content.approval_id


def resolve_with(sm, d: Driver, workflow_id: str, content_id: str | None):
    payload = {"approval_id": workflow_id}
    if content_id is not None:
        payload["content_approval_id"] = content_id
    return sm.apply(d.case_id, Event.HUMAN_RESOLVED_CASE, payload, Component.EXTERNAL)


# --- the seam ------------------------------------------------------------------------------

def test_a_valid_content_approval_is_recorded_on_the_row_and_consumed(sm, app_engine):
    d = escalated(sm, app_engine)
    execution_id = write_execution(app_engine, d)
    workflow_id, content_id = write_approvals(app_engine, d, execution_id)

    result = resolve_with(sm, d, workflow_id, content_id)

    assert result.committed and result.state_after is State.COMPLETED
    row = d.trace()[-1]
    # §5's postcondition: the answer is documented, with the approval and the exact text.
    assert row.event == Event.HUMAN_RESOLVED_CASE.value
    assert row.action == Action.ANSWER_CLINICAL_QUESTION.value
    assert row.content_hash == ANSWER_HASH
    assert row.execution_id == execution_id
    assert row.approval_id == workflow_id  # the transition's own approval is the WorkflowDecision
    with app_engine.connect() as conn:
        assert repository.load_approval(conn, content_id).consumed_at is not None
        assert repository.load_approval(conn, workflow_id).consumed_at is not None


def test_a_resolve_without_an_answer_is_unchanged(sm, app_engine):
    """The plain close keeps working, and carries no action or content hash."""
    d = escalated(sm, app_engine)
    workflow_id, _ = write_approvals(app_engine, d, write_execution(app_engine, d))

    result = resolve_with(sm, d, workflow_id, None)

    assert result.committed and result.state_after is State.COMPLETED
    row = d.trace()[-1]
    assert row.action is None and row.content_hash is None


@pytest.mark.parametrize("changes", [
    {"approval_type": "WorkflowDecision"},                        # not a content approval
    {"reviewer_role": "admin_staff"},                             # §12.4: clinical_staff only
    {"action": Action.SEND_STATUS_UPDATE.value},                  # another action's approval
    {"content_hash": None},                                       # §12.4 requires it
    {"execution_id": None},                                       # §12.4 requires it
    {"consumed_at": datetime(2026, 1, 1, tzinfo=UTC)},            # single use
    {"valid_until": datetime(2020, 1, 1, tzinfo=UTC)},            # expired
])
def test_an_unusable_content_approval_blocks_the_transition(sm, app_engine, changes):
    d = escalated(sm, app_engine)
    execution_id = write_execution(app_engine, d)
    workflow_id, content_id = write_approvals(app_engine, d, execution_id, **changes)

    result = resolve_with(sm, d, workflow_id, content_id)

    assert not result.committed and result.reason == "content_approval_invalid"
    assert d.state is State.AWAITING_HUMAN_REVIEW
    with app_engine.connect() as conn:
        # Fail closed: nothing was consumed, so the reviewer can try again. Where the
        # parametrised approval was already consumed going in (the "single use" case),
        # it stays exactly as it was - the blocked transition adds no further consumption.
        assert repository.load_approval(conn, workflow_id).consumed_at is None
        assert repository.load_approval(conn, content_id).consumed_at == changes.get("consumed_at")


def test_a_content_approval_belonging_to_another_case_is_refused(sm, app_engine):
    """The brief's parametrised {"case_id": "CASE-SOMEONE-ELSE"} cannot work as written:
    approvals.case_id has a FOREIGN KEY to cases.case_id (db.py), so that row would fail at
    INSERT and test the database rather than the seam. This builds a second real case with
    `escalated()` and uses its case_id instead, keeping the same intent: an approval that
    belongs to a different (but real) case is refused."""
    d = escalated(sm, app_engine)
    other = escalated(sm, app_engine)
    execution_id = write_execution(app_engine, d)
    workflow_id, content_id = write_approvals(app_engine, d, execution_id, case_id=other.case_id,
                                               patient_id=other.patient_id)

    result = resolve_with(sm, d, workflow_id, content_id)

    assert not result.committed and result.reason == "content_approval_invalid"
    assert d.state is State.AWAITING_HUMAN_REVIEW
    with app_engine.connect() as conn:
        assert repository.load_approval(conn, content_id).consumed_at is None


def test_an_approval_whose_execution_belongs_to_another_case_is_refused(sm, app_engine):
    d = escalated(sm, app_engine)
    other = escalated(sm, app_engine)
    workflow_id, content_id = write_approvals(app_engine, d, write_execution(app_engine, other))

    result = resolve_with(sm, d, workflow_id, content_id)

    assert not result.committed and result.reason == "content_approval_invalid"


def test_an_execution_whose_hash_differs_from_the_approval_is_refused(sm, app_engine):
    d = escalated(sm, app_engine)
    execution_id = write_execution(app_engine, d, content_hash="a different hash")
    workflow_id, content_id = write_approvals(app_engine, d, execution_id)

    result = resolve_with(sm, d, workflow_id, content_id)

    assert not result.committed and result.reason == "content_approval_invalid"


def test_content_approval_id_is_ignored_on_any_other_event(sm, app_engine):
    """The seam is one event wide: an unrelated event never consumes an approval."""
    d = escalated(sm, app_engine)
    execution_id = write_execution(app_engine, d)
    # WorkflowDecisionValid (fsm.py) requires decision=="reject" for HUMAN_REJECTED - the
    # brief's write_approvals() default ("resolve") is for HUMAN_RESOLVED_CASE.
    workflow_id, content_id = write_approvals(app_engine, d, execution_id, workflow_decision="reject")

    result = sm.apply(d.case_id, Event.HUMAN_REJECTED,
                      {"approval_id": workflow_id, "content_approval_id": content_id}, Component.EXTERNAL)

    assert result.committed and result.state_after is State.FAILED
    assert d.trace()[-1].content_hash is None
    with app_engine.connect() as conn:
        assert repository.load_approval(conn, content_id).consumed_at is None
