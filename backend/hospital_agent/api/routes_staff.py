"""/api/staff: the staff screen's API - the Case Monitor, the review queue, the shown
context, the decision and a Data Log tombstone (§1, §12.4-§12.5, §18.4; design §5, §6).

The reviewer's identity is the token's (§18.3). A decision that cannot be a legal
transition, or that was taken on a context that has since changed, is refused with the
reason code the Human Review Service gives - the API never decides that itself.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.engine import Engine

from .. import document_status, llm_costs, metrics, repository
from ..auth import Principal
from ..human_review import AnswerRejected, ContextChanged, DecisionRejected, HumanReviewService, NotInReview
from ..llm import telemetry
from ..naming import EscalationKind, State
from ..session import CaseNotFound
from ..state_groups import STATE_GROUPS
from . import appointments, instructions
from .deps import get_engine, get_reviews, require_staff
from .schemas import (
    AnswerRequest,
    AppointmentsView,
    AuditRecord,
    CaseDetail,
    CaseListPage,
    CaseLlmUsageView,
    CaseSummary,
    DecisionRequest,
    DecisionResponse,
    InstructionView,
    LlmCostsResponse,
    MessageTemplateView,
    PatientRequestBody,
    ReviewContext,
    ReviewItem,
    ReviewQueuePage,
    SystemStatusView,
)

router = APIRouter(prefix="/api/staff", tags=["staff"], dependencies=[Depends(require_staff)])
logger = logging.getLogger(__name__)

MAX_LIST_LIMIT = 200
DEFAULT_LIST_LIMIT = 50


# --- the Case Monitor (Core design §9), now behind staff auth --------------------------------

@router.get("/cases", response_model=CaseListPage)
def list_cases(state: State | None = None, group: str | None = None, escalation_kind: str | None = None,
               limit: int = DEFAULT_LIST_LIMIT, cursor: str | None = None,
               db: Engine = Depends(get_engine)) -> CaseListPage:
    """Staff-fixes design Task 3/4: one call, keyset-paginated, optionally filtered by an
    exact `state` or by one of `STATE_GROUPS` - `?group=staff` also accepts
    `?escalation_kind=`, since only that group carries one. Every column the Case Monitor
    table shows, so the client makes no per-row `getCase` follow-up."""
    if not (1 <= limit <= MAX_LIST_LIMIT):
        raise HTTPException(status_code=422, detail="invalid_limit")
    if state is not None and group is not None:
        # Fix round 1 (I3): the two are alternative filters, not a narrower combination of
        # both - silently picking one of them would surprise whichever the client thought
        # was in effect.
        raise HTTPException(status_code=422, detail="invalid_filter")
    if escalation_kind is not None and group != "staff":
        raise HTTPException(status_code=422, detail="invalid_filter")
    parsed_kind: EscalationKind | None = None
    if escalation_kind is not None:
        try:
            parsed_kind = EscalationKind(escalation_kind)
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid_filter") from None
    states: list[State] | None
    if group is not None:
        if group not in STATE_GROUPS:
            raise HTTPException(status_code=422, detail="invalid_filter")
        states = list(STATE_GROUPS[group])
    elif state is not None:
        states = [state]
    else:
        states = None
    parsed_cursor = None
    if cursor is not None:
        try:
            parsed_cursor = repository.decode_cases_cursor(cursor)
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid_cursor") from None
    with db.connect() as conn:
        rows, has_more = repository.list_cases_page(conn, states, limit, parsed_cursor, escalation_kind=parsed_kind)
        # Sub-project 19 (design D6): the page's costs in one grouped query, never one per row.
        costs = llm_costs.case_costs(conn, [row.case_id for row in rows])
    next_cursor = repository.encode_cases_cursor(rows[-1].updated_at, rows[-1].case_id) if has_more and rows else None
    items = [CaseSummary.model_validate(row).model_copy(update={
        "llm_cost_usd": costs[row.case_id].cost_usd, "llm_cost_partial": costs[row.case_id].partial,
        "llm_unpriced_calls": costs[row.case_id].unpriced_calls}) for row in rows]
    return CaseListPage(items=items, next_cursor=next_cursor)


@router.get("/cases/{case_id}", response_model=CaseDetail)
def get_case(case_id: str, db: Engine = Depends(get_engine)) -> CaseDetail:
    with db.connect() as conn:
        case = repository.load_case(conn, case_id)
        usage = llm_costs.case_usage(conn, case_id) if case is not None else None
    if case is None:
        raise HTTPException(status_code=404, detail="case_not_found")
    return CaseDetail.model_validate(case).model_copy(update={"llm_usage": CaseLlmUsageView.model_validate(usage)})


@router.get("/llm-costs", response_model=LlmCostsResponse)
def get_llm_costs(start: str = Query(alias="from"), end: str = Query(alias="to"),
                  db: Engine = Depends(get_engine)) -> LlmCostsResponse:
    """Sub-project 19 (design D6): the LLM cost of the cases opened in [from, to) - the admin
    metrics' window rules (ISO-8601 with a time zone, at most 90 days) and snapshot, for any
    staff member. Aggregates only: no case id, patient id or model name leaves here."""
    try:
        window = metrics.Window.parse(start, end)
    except metrics.InvalidWindow as invalid:
        raise HTTPException(status_code=422, detail=invalid.code) from None
    try:
        return LlmCostsResponse.model_validate(metrics.compute_llm(db, window))
    except metrics.MetricsUnavailable:
        logger.warning("llm costs unavailable: statement timeout")
        raise HTTPException(status_code=503, detail="llm_costs_unavailable") from None


@router.get("/cases/{case_id}/appointments", response_model=AppointmentsView)
def case_appointments(case_id: str, request: Request, start: str | None = Query(default=None, alias="from"),
                      end: str | None = Query(default=None, alias="to"),
                      db: Engine = Depends(get_engine)) -> AppointmentsView:
    """The case's patient's appointments (sub-project 16). Apart from `/context` on purpose:
    the list is not part of what a decision is bound to (`shown_context_ref`)."""
    with db.connect() as conn:
        case = repository.load_case(conn, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case_not_found")
    return appointments.read(request.app.state.appointment_list, case.patient_id, start, end)


@router.get("/instructions/{source_id}", response_model=InstructionView)
def get_instruction(source_id: str, request: Request,
                    version: str | None = Query(default=None)) -> InstructionView:
    """Sub-project 18 (design D12, D13): the same instruction read as the patient's, for staff
    viewing a case's appointments (Case Monitor row detail / review context). The router-level
    `require_staff` dependency already gates every route in this file."""
    return instructions.read(request.app.state.instructions_client, source_id, version)


@router.get("/cases/{case_id}/audit", response_model=list[AuditRecord])
def get_audit(case_id: str, db: Engine = Depends(get_engine)) -> list[AuditRecord]:
    with db.connect() as conn:
        if repository.load_case(conn, case_id) is None:
            raise HTTPException(status_code=404, detail="case_not_found")
        return [AuditRecord.model_validate(entry) for entry in repository.load_trace(conn, case_id)]


@router.get("/system-status", response_model=SystemStatusView)
def system_status(request: Request) -> SystemStatusView:
    """Staff-fixes design Task 1, decision 3: whether the Agent Orchestrator runs, and the
    LLM's last outcome - shown as a banner while the last call failed or it does not run.
    Row 98: and the document-service - a live /health check (when it is configured) beside the
    last upload's outcome, so staff see an outage before cases start expiring."""
    intake = request.app.state.session.document_intake
    health = intake.health() if intake is not None and hasattr(intake, "health") else None
    return SystemStatusView(orchestrator=request.app.state.orchestrator_status, llm=telemetry.status(),
                            documents=document_status.status(health))


# --- human review -----------------------------------------------------------------------------

@router.get("/reviews", response_model=ReviewQueuePage)
def review_queue(limit: int = DEFAULT_LIST_LIMIT, cursor: str | None = None,
                 reviews: HumanReviewService = Depends(get_reviews)) -> ReviewQueuePage:
    """Staff-fixes design Task 5: one call, keyset-paginated, newest entry into
    AwaitingHumanReview first."""
    if not (1 <= limit <= MAX_LIST_LIMIT):
        raise HTTPException(status_code=422, detail="invalid_limit")
    parsed_cursor = None
    if cursor is not None:
        try:
            parsed_cursor = repository.decode_queue_cursor(cursor)
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid_cursor") from None
    items, has_more = reviews.queue(limit=limit, cursor=parsed_cursor)
    next_cursor = (repository.encode_queue_cursor(items[-1].entered_at, items[-1].case_id)
                  if has_more and items else None)
    return ReviewQueuePage(items=[ReviewItem.model_validate(item) for item in items], next_cursor=next_cursor)


@router.get("/reviews/{case_id}", response_model=ReviewItem)
def review_item(case_id: str, reviews: HumanReviewService = Depends(get_reviews)) -> ReviewItem:
    """Staff-fixes design Task 3: one queue item, so `ReviewCase` stops fetching the whole
    queue just to find its own row."""
    try:
        return ReviewItem.model_validate(reviews.queue_item(case_id))
    except CaseNotFound:
        raise HTTPException(status_code=404, detail="case_not_found") from None
    except NotInReview:
        raise HTTPException(status_code=404, detail="not_in_review") from None


@router.get("/cases/{case_id}/context", response_model=ReviewContext)
def review_context(case_id: str, reviews: HumanReviewService = Depends(get_reviews)) -> ReviewContext:
    try:
        return ReviewContext.model_validate(reviews.context(case_id))
    except CaseNotFound:
        raise HTTPException(status_code=404, detail="case_not_found") from None


@router.post("/cases/{case_id}/decision", response_model=DecisionResponse)
def decide(case_id: str, body: DecisionRequest, principal: Principal = Depends(require_staff),
           reviews: HumanReviewService = Depends(get_reviews)) -> DecisionResponse:
    try:
        reviews.decide(
            reviewer_id=principal.user_id,
            reviewer_role=principal.role,
            case_id=case_id,
            decision=body.decision,
            reason=body.reason,
            shown_context_ref=body.shown_context_ref,
            verified_identity_ref=body.verified_identity_ref,
            patient_deadline=body.patient_deadline,
            message=body.message.model_dump() if body.message else None,
        )
    except CaseNotFound:
        raise HTTPException(status_code=404, detail="case_not_found") from None
    except NotInReview:
        raise HTTPException(status_code=409, detail="not_in_review") from None
    except ContextChanged:
        raise HTTPException(status_code=409, detail="context_changed") from None
    except DecisionRejected as rejected:
        status = 403 if rejected.reason == "clinical_staff_only" else 409
        raise HTTPException(status_code=status, detail=rejected.reason) from None
    return DecisionResponse(case_id=case_id, state=reviews.sm.load(case_id).state.value)


@router.get("/message-templates", response_model=list[MessageTemplateView])
def message_templates(reviews: HumanReviewService = Depends(get_reviews)) -> list[MessageTemplateView]:
    """Sub-project 15: the fixed messages (design §7.1) - the UI keeps no copy of them."""
    return [MessageTemplateView.model_validate(template) for template in reviews.templates()]


@router.post("/cases/{case_id}/request", response_model=DecisionResponse)
def request_from_patient(case_id: str, body: PatientRequestBody, principal: Principal = Depends(require_staff),
                         reviews: HumanReviewService = Depends(get_reviews)) -> DecisionResponse:
    """Sub-project 15 (design §5, §7): ask the patient a question or for one catalog document."""
    try:
        reviews.request(
            reviewer_id=principal.user_id, reviewer_role=principal.role, case_id=case_id, kind=body.kind,
            reason=body.reason, shown_context_ref=body.shown_context_ref, template_id=body.template_id,
            param=body.param, text=body.text, document_type=body.document_type, deadline=body.deadline)
    except CaseNotFound:
        raise HTTPException(status_code=404, detail="case_not_found") from None
    except NotInReview:
        raise HTTPException(status_code=409, detail="not_in_review") from None
    except ContextChanged:
        raise HTTPException(status_code=409, detail="context_changed") from None
    except DecisionRejected as rejected:
        status = 403 if rejected.reason == "clinical_staff_only" else 409
        raise HTTPException(status_code=status, detail=rejected.reason) from None
    return DecisionResponse(case_id=case_id, state=reviews.sm.load(case_id).state.value)


@router.post("/cases/{case_id}/answer", response_model=DecisionResponse)
def answer(case_id: str, body: AnswerRequest, principal: Principal = Depends(require_staff),
           reviews: HumanReviewService = Depends(get_reviews)) -> DecisionResponse:
    """§5, §12.4: a clinical answer, authorised by a ContentApproval bound to its exact text."""
    try:
        reviews.answer(
            reviewer_id=principal.user_id,
            reviewer_role=principal.role,
            case_id=case_id,
            answer=body.answer,
            reason=body.reason,
            shown_context_ref=body.shown_context_ref,
        )
    except CaseNotFound:
        raise HTTPException(status_code=404, detail="case_not_found") from None
    except NotInReview:
        raise HTTPException(status_code=409, detail="not_in_review") from None
    except ContextChanged:
        raise HTTPException(status_code=409, detail="context_changed") from None
    except AnswerRejected as rejected:
        status = 403 if rejected.reason == "clinical_staff_only" else 409
        raise HTTPException(status_code=status, detail=rejected.reason) from None
    return DecisionResponse(case_id=case_id, state=reviews.sm.load(case_id).state.value)


@router.delete("/cases/{case_id}/data/{entry_id}", status_code=204)
def tombstone(case_id: str, entry_id: str, reviews: HumanReviewService = Depends(get_reviews)) -> Response:
    """§18.4: clear one Data Log entry of this case. Audit is never touched."""
    if not reviews.tombstone(case_id, entry_id):
        raise HTTPException(status_code=404, detail="entry_not_found")
    return Response(status_code=204)
