"""Clinical free text to the patient (sub-project 15, design §7.3): bound by a ContentApproval
exactly like the clinical answer, but under its own payload key and verifier."""
import uuid
from datetime import UTC, datetime, timedelta

from hospital_agent import data_log, repository
from hospital_agent.case import ApprovalRecord, ExecutionRecord
from hospital_agent.naming import Action, Component, Event, State
from tests.test_patient_request_fsm import escalated


def clinical_message(d, text="נא לפרט את מועד התור ואת המחלקה", role="clinical_staff") -> tuple[str, str]:
    """What HumanReviewService does for free text (Task 6): the Data Log entry, the execution row
    and the ContentApproval bound to them. Returns (approval_id, content_hash)."""
    case, now = d.case, datetime.now(UTC)
    with d.engine.begin() as conn:
        entry = data_log.record(conn, case.case_id, case.patient_id, data_log.DataKind.STAFF_MESSAGE, text, now)
        execution_id = f"EXEC-{uuid.uuid4().hex[:12]}"
        repository.insert_execution(conn, ExecutionRecord(
            execution_id=execution_id, case_id=case.case_id, patient_id=case.patient_id,
            action=Action.ANSWER_CLINICAL_QUESTION.value, step=case.current_step or 0,
            retry_cycle=case.retry_cycle, attempt_number=0,
            idempotency_key=f"{case.case_id}:message:{execution_id}", status="succeeded",
            state_version=case.state_version, content_hash=entry.content_hash, medical_content_flag=True))
        approval_id = f"APPR-{uuid.uuid4().hex[:12]}"
        repository.insert_approval(conn, ApprovalRecord(
            approval_id=approval_id, approval_type="ContentApproval", case_id=case.case_id,
            patient_id=case.patient_id, reviewer_id="coordinator_nurse", reviewer_role=role, decision="approve",
            reason="clinical message", shown_context_ref="ctx", granted_at=now - timedelta(minutes=1),
            valid_until=now + timedelta(hours=1), execution_id=execution_id,
            action=Action.ANSWER_CLINICAL_QUESTION.value, content_hash=entry.content_hash))
    return approval_id, entry.content_hash


def request_with(sm, d, message_approval_id, content_hash):
    payload = {"approval_id": d.approval("request", patient_deadline=datetime.now(UTC) + timedelta(hours=24)),
               "reply_kind": "question", "requested_document": None, "content_hash": content_hash,
               "message_approval_id": message_approval_id}
    return sm.apply(d.case_id, Event.PATIENT_REPLY_REQUESTED, payload, Component.EXTERNAL)


def _approval(d, approval_id):
    with d.engine.connect() as conn:
        return repository.load_approval(conn, approval_id)


def test_a_clinical_message_rides_on_a_request_and_is_consumed(sm, app_engine):
    d = escalated(sm, app_engine)
    approval_id, content_hash = clinical_message(d)
    result = request_with(sm, d, approval_id, content_hash)
    assert result.committed and result.state_after is State.AWAITING_PATIENT_REPLY
    assert _approval(d, approval_id).consumed_at is not None
    committed = [r for r in d.trace() if r.event == "PATIENT_REPLY_REQUESTED" and r.record_type == "Transition"]
    assert committed[-1].content_hash == content_hash


def test_an_approval_granted_by_admin_staff_is_refused(sm, app_engine):
    d = escalated(sm, app_engine)
    approval_id, content_hash = clinical_message(d, role="admin_staff")
    result = request_with(sm, d, approval_id, content_hash)
    assert not result.committed and result.reason == "content_approval_invalid"
    assert d.state is State.AWAITING_HUMAN_REVIEW


def test_a_message_approval_on_approve_is_refused(sm, app_engine):
    d = escalated(sm, app_engine)
    approval_id, _ = clinical_message(d)
    result = sm.apply(d.case_id, Event.HUMAN_APPROVED,
                      {"approval_id": d.approval("approve"), "message_approval_id": approval_id}, Component.EXTERNAL)
    assert not result.committed and result.reason == "content_approval_invalid"


def test_both_approval_keys_at_once_are_refused(sm, app_engine):
    d = escalated(sm, app_engine)
    approval_id, _ = clinical_message(d)
    result = sm.apply(d.case_id, Event.HUMAN_RESOLVED_CASE,
                      {"approval_id": d.approval("resolve"), "message_approval_id": approval_id,
                       "content_approval_id": approval_id}, Component.EXTERNAL)
    assert not result.committed and result.reason == "content_approval_invalid"


def test_a_closing_message_rides_on_reject(sm, app_engine):
    d = escalated(sm, app_engine)
    approval_id, content_hash = clinical_message(d, text="לא נוכל לטפל בפנייה, נא לפנות למרפאה")
    result = sm.apply(d.case_id, Event.HUMAN_REJECTED,
                      {"approval_id": d.approval("reject"), "message_approval_id": approval_id,
                       "content_hash": content_hash}, Component.EXTERNAL)
    assert result.committed and result.state_after is State.FAILED
    assert _approval(d, approval_id).consumed_at is not None


def test_a_used_message_approval_cannot_be_used_twice(sm, app_engine):
    d = escalated(sm, app_engine)
    approval_id, content_hash = clinical_message(d)
    assert request_with(sm, d, approval_id, content_hash).committed
    sm.apply(d.case_id, Event.PATIENT_REPLY_SUBMITTED, {"reply_kind": "question", "content_hash": "HASH-REPLY"},
             Component.SESSION_SERVICE)
    result = sm.apply(d.case_id, Event.HUMAN_RESOLVED_CASE,
                      {"approval_id": d.approval("resolve"), "message_approval_id": approval_id}, Component.EXTERNAL)
    assert not result.committed and result.reason == "content_approval_invalid"
