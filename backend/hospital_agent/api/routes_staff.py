"""/api/staff: the staff screen's API - the Case Monitor, the review queue, the shown
context, the decision and a Data Log tombstone (§1, §12.4-§12.5, §18.4; design §5, §6).

The reviewer's identity is the token's (§18.3). A decision that cannot be a legal
transition, or that was taken on a context that has since changed, is refused with the
reason code the Human Review Service gives - the API never decides that itself.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.engine import Engine

from .. import repository
from ..auth import Principal
from ..human_review import ContextChanged, DecisionRejected, HumanReviewService, NotInReview
from ..naming import State
from ..session import CaseNotFound
from .deps import get_engine, get_reviews, require_staff
from .schemas import (
    AuditRecord,
    CaseDetail,
    CaseSummary,
    DecisionRequest,
    DecisionResponse,
    ReviewContext,
    ReviewItem,
)

router = APIRouter(prefix="/api/staff", tags=["staff"], dependencies=[Depends(require_staff)])


# --- the Case Monitor (Core design §9), now behind staff auth --------------------------------

@router.get("/cases", response_model=list[CaseSummary])
def list_cases(state: State | None = None, db: Engine = Depends(get_engine)) -> list[CaseSummary]:
    with db.connect() as conn:
        return [CaseSummary.model_validate(case) for case in repository.list_cases(conn, state)]


@router.get("/cases/{case_id}", response_model=CaseDetail)
def get_case(case_id: str, db: Engine = Depends(get_engine)) -> CaseDetail:
    with db.connect() as conn:
        case = repository.load_case(conn, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case_not_found")
    return CaseDetail.model_validate(case)


@router.get("/cases/{case_id}/audit", response_model=list[AuditRecord])
def get_audit(case_id: str, db: Engine = Depends(get_engine)) -> list[AuditRecord]:
    with db.connect() as conn:
        if repository.load_case(conn, case_id) is None:
            raise HTTPException(status_code=404, detail="case_not_found")
        return [AuditRecord.model_validate(entry) for entry in repository.load_trace(conn, case_id)]


# --- human review -----------------------------------------------------------------------------

@router.get("/reviews", response_model=list[ReviewItem])
def review_queue(reviews: HumanReviewService = Depends(get_reviews)) -> list[ReviewItem]:
    return [ReviewItem.model_validate(item) for item in reviews.queue()]


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
        )
    except CaseNotFound:
        raise HTTPException(status_code=404, detail="case_not_found") from None
    except NotInReview:
        raise HTTPException(status_code=409, detail="not_in_review") from None
    except ContextChanged:
        raise HTTPException(status_code=409, detail="context_changed") from None
    except DecisionRejected as rejected:
        raise HTTPException(status_code=409, detail=rejected.reason) from None
    return DecisionResponse(case_id=case_id, state=reviews.sm.load(case_id).state.value)


@router.delete("/cases/{case_id}/data/{entry_id}", status_code=204)
def tombstone(case_id: str, entry_id: str, reviews: HumanReviewService = Depends(get_reviews)) -> Response:
    """§18.4: clear one Data Log entry of this case. Audit is never touched."""
    if not reviews.tombstone(case_id, entry_id):
        raise HTTPException(status_code=404, detail="entry_not_found")
    return Response(status_code=204)
