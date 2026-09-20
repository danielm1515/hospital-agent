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
        content_hash=ANSWER_HASH, escalation_kind=None,
        # §12.4: production always grants a ContentApproval with decision="approve"
        # (`human_review._grant_content_approval`), independent of the WorkflowDecision's
        # own decision - keep the two decoupled here too.
        decision="approve")
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
    {"decision": "reject"},                                       # §12.4: only an approve decision authorises content
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


def test_a_content_approval_is_refused_once_the_case_is_no_longer_a_medical_question(sm, app_engine):
    """Design decision 2's other half: `_clinical_answer_approval` also checks that the case
    is *currently* escalated as MedicalQuestion, not just that the approval looks well-formed -
    defence in depth alongside human_review.answer()'s own check of the same thing."""
    d = Driver(sm, app_engine)
    d.to_classified()
    d.plan()
    d.propose()
    d.allow()
    d.retry_exhausted()
    assert d.case.escalation_kind is EscalationKind.RETRY_EXHAUSTED
    execution_id = write_execution(app_engine, d)
    workflow_id, content_id = write_approvals(app_engine, d, execution_id)

    result = resolve_with(sm, d, workflow_id, content_id)

    assert not result.committed and result.reason == "content_approval_invalid"
    assert d.state is State.AWAITING_HUMAN_REVIEW
    with app_engine.connect() as conn:
        assert repository.load_approval(conn, content_id).consumed_at is None


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


# --- the service ---------------------------------------------------------------------------

from hospital_agent import data_log                                    # noqa: E402
from hospital_agent.human_review import AnswerRejected, ContextChanged, HumanReviewService, NotInReview  # noqa: E402
from hospital_agent.session import SessionService                      # noqa: E402
from hospital_agent.state_manager import TransitionResult               # noqa: E402

NURSE = ("coordinator_nurse", "clinical_staff")
ADMIN = ("admin_coordinator", "admin_staff")


@pytest.fixture
def session(sm):
    return SessionService(sm)


@pytest.fixture
def review(sm, session):
    """The same two-line fixture `tests/test_human_review.py` uses."""
    return HumanReviewService(sm, session)


def answer_it(review, d, *, who=NURSE, answer=ANSWER, reason="נענתה בטלפון על ידי האחות"):
    return review.answer(reviewer_id=who[0], reviewer_role=who[1], case_id=d.case_id,
                         answer=answer, reason=reason,
                         shown_context_ref=review.context(d.case_id).shown_context_ref)


def approvals_of(app_engine, case_id):
    with app_engine.connect() as conn:
        return {a.approval_type: a for a in repository.content_approvals_for(conn, case_id, None)}


def test_a_nurse_answers_and_the_case_completes(review, sm, app_engine):
    d = escalated(sm, app_engine)

    result = answer_it(review, d)

    assert result.committed and result.state_after is State.COMPLETED
    row = d.trace()[-1]
    assert row.action == Action.ANSWER_CLINICAL_QUESTION.value
    with app_engine.connect() as conn:
        [message] = [e for e in data_log.entries(conn, d.case_id, data_log.DataKind.OUTGOING_MESSAGE)]
    assert message.content == ANSWER and row.content_hash == message.content_hash
    both = approvals_of(app_engine, d.case_id)
    assert both["ContentApproval"].consumed_at is not None
    assert both["WorkflowDecision"].consumed_at is not None
    assert both["ContentApproval"].reviewer_role == "clinical_staff"


def test_the_execution_row_is_final_so_restart_recovery_ignores_it(review, sm, app_engine):
    """Decision 4: a row left in 'started' would escalate as ExecutionUnknown after a restart."""
    d = escalated(sm, app_engine)
    answer_it(review, d)
    with app_engine.connect() as conn:
        executions = repository.executions_of_case(conn, d.case_id)
    assert [e.status for e in executions] == ["succeeded"]
    assert executions[0].medical_content_flag is True


def test_admin_staff_may_not_answer_and_nothing_is_written(review, sm, app_engine):
    d = escalated(sm, app_engine)
    with pytest.raises(AnswerRejected) as rejected:
        answer_it(review, d, who=ADMIN)
    assert rejected.value.reason == "clinical_staff_only"
    assert d.state is State.AWAITING_HUMAN_REVIEW
    with app_engine.connect() as conn:
        assert data_log.entries(conn, d.case_id, data_log.DataKind.OUTGOING_MESSAGE) == []
    assert approvals_of(app_engine, d.case_id) == {}


def test_an_escalation_that_is_not_a_medical_question_is_refused(review, sm, app_engine):
    """A RetryExhausted case is in review too - but it is not a question to answer."""
    d = Driver(sm, app_engine)
    d.to_classified()
    d.plan()
    d.propose()
    d.allow()
    d.retry_exhausted()
    assert d.case.escalation_kind is EscalationKind.RETRY_EXHAUSTED
    with pytest.raises(AnswerRejected) as rejected:
        answer_it(review, d)
    assert rejected.value.reason == "decision_not_allowed"


@pytest.mark.parametrize("field, value, expected", [
    ("answer", "   ", "answer_required"),
    ("reason", "   ", "reason_required"),
])
def test_empty_input_is_refused(review, sm, app_engine, field, value, expected):
    d = escalated(sm, app_engine)
    with pytest.raises(AnswerRejected) as rejected:
        answer_it(review, d, **{field: value})
    assert rejected.value.reason == expected


def test_a_stale_shown_context_ref_is_refused(review, sm, app_engine):
    d = escalated(sm, app_engine)
    with pytest.raises(ContextChanged):
        review.answer(reviewer_id=NURSE[0], reviewer_role=NURSE[1], case_id=d.case_id,
                       answer=ANSWER, reason="סיבה", shown_context_ref="ctx-stale")
    assert d.state is State.AWAITING_HUMAN_REVIEW


def test_a_case_that_is_not_in_review_is_refused(review, sm, app_engine):
    d = Driver(sm, app_engine)
    d.submit()
    with pytest.raises(NotInReview):
        answer_it(review, d)


def test_a_blocked_transition_tombstones_the_recorded_answer(review, sm, app_engine, monkeypatch):
    """Fail closed (§14): if HUMAN_RESOLVED_CASE does not commit, the text just recorded in
    the Data Log must not stay readable - it was never authorised to be shown."""
    d = escalated(sm, app_engine)
    real_apply = sm.apply

    def blocked_resolve(case_id, event, payload=None, source=Component.EXTERNAL, **kwargs):
        if event is Event.HUMAN_RESOLVED_CASE and (payload or {}).get("content_approval_id"):
            case = sm.load(case_id)
            return TransitionResult(case_id=case_id, committed=False, state_before=case.state,
                                     state_after=case.state, reason="content_approval_invalid")
        return real_apply(case_id, event, payload, source, **kwargs)

    monkeypatch.setattr(sm, "apply", blocked_resolve)

    with pytest.raises(AnswerRejected) as rejected:
        answer_it(review, d)

    assert rejected.value.reason == "content_approval_invalid"
    assert d.state is State.AWAITING_HUMAN_REVIEW
    with app_engine.connect() as conn:
        [message] = data_log.entries(conn, d.case_id, data_log.DataKind.OUTGOING_MESSAGE)
    assert message.content is None
    assert message.deleted_at is not None


def test_a_raising_transition_tombstones_the_recorded_answer_too(review, sm, app_engine, monkeypatch):
    """The other fail-closed exit (§12.3): sm.apply() can raise instead of returning a
    not-committed result - ReprocessLimitExceeded, or the Temporal Monitor being unavailable,
    which by design lets its exception through. The text must not stay readable then either."""
    d = escalated(sm, app_engine)
    real_apply = sm.apply

    class _MonitorUnavailable(RuntimeError):
        pass

    def raising_resolve(case_id, event, payload=None, source=Component.EXTERNAL, **kwargs):
        if event is Event.HUMAN_RESOLVED_CASE and (payload or {}).get("content_approval_id"):
            raise _MonitorUnavailable("temporal monitor unavailable")
        return real_apply(case_id, event, payload, source, **kwargs)

    monkeypatch.setattr(sm, "apply", raising_resolve)

    with pytest.raises(_MonitorUnavailable):
        answer_it(review, d)

    assert d.state is State.AWAITING_HUMAN_REVIEW
    with app_engine.connect() as conn:
        [message] = data_log.entries(conn, d.case_id, data_log.DataKind.OUTGOING_MESSAGE)
    assert message.content is None
    assert message.deleted_at is not None


# --- what the patient sees -----------------------------------------------------------------

def test_the_patient_sees_the_answer_as_a_delivered_message(review, session, sm, app_engine):
    d = escalated(sm, app_engine)
    answer_it(review, d)

    view = session.patient_view(d.case_id)

    assert (view.status, view.message) == ("completed", ANSWER)
    assert [step.status for step in view.history][-1] == "completed"
    # §12.3: still no kind, no reason, no audit.
    assert not hasattr(view, "escalation_kind")


def test_a_case_closed_without_an_answer_stays_closed(review, session, sm, app_engine):
    d = escalated(sm, app_engine)
    review.decide(reviewer_id=NURSE[0], reviewer_role=NURSE[1], case_id=d.case_id,
                   decision="resolve", reason="הופנתה למרפאה",
                   shown_context_ref=review.context(d.case_id).shown_context_ref)

    view = session.patient_view(d.case_id)

    assert (view.status, view.message) == ("closed", None)


def test_a_deleted_answer_is_not_shown_again(review, session, sm, app_engine):
    """§18.4: a tombstone leaves the hash and the approval, but no content to show."""
    d = escalated(sm, app_engine)
    answer_it(review, d)
    with app_engine.connect() as conn:
        [entry] = data_log.entries(conn, d.case_id, data_log.DataKind.OUTGOING_MESSAGE)
    assert review.tombstone(d.case_id, entry.entry_id)

    view = session.patient_view(d.case_id)

    assert (view.status, view.message) == ("closed", None)


def test_a_message_without_a_matching_approval_is_never_shown(session, sm, app_engine):
    """The read-side stand-in for T6: no approval, no medical content on the screen."""
    d = escalated(sm, app_engine)
    with app_engine.begin() as conn:
        data_log.record(conn, d.case_id, d.patient_id, data_log.DataKind.OUTGOING_MESSAGE,
                        "טקסט שאיש לא אישר", sm.clock())
    workflow_id, _ = write_approvals(app_engine, d, write_execution(app_engine, d))
    assert resolve_with(sm, d, workflow_id, None).committed

    view = session.patient_view(d.case_id)

    assert (view.status, view.message) == ("closed", None)


def test_a_later_unapproved_message_never_displaces_the_approved_answer(review, session, sm, app_engine):
    """`_clinical_answer` takes the newest approved message (`answers[-1]`) - pin that a
    later, unapproved outgoing message in the Data Log can never sort ahead of it and be
    shown instead. This is the ordering the seam actually depends on, not just presence."""
    d = escalated(sm, app_engine)
    answer_it(review, d)
    unapproved = "עדכון מאוחר שאיש לא אישר"
    with app_engine.begin() as conn:
        data_log.record(conn, d.case_id, d.patient_id, data_log.DataKind.OUTGOING_MESSAGE,
                        unapproved, sm.clock() + timedelta(seconds=1))

    view = session.patient_view(d.case_id)

    assert (view.status, view.message) == ("completed", ANSWER)
    assert unapproved not in str(view)
