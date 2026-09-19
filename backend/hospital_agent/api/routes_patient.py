"""/api/patient: the patient screen's API (§1, §12.3, D24; design §4, §6).

Every route works on the patient the token names. A case that belongs to someone else is
404, exactly like a case that does not exist - the API never reveals that it exists. The
answer is always the abstract PatientCaseView: no escalation kind, no reason, no Audit.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from ..auth import DEMO_USERS, Principal
from ..session import CaseNotFound, EventRejected, SessionService
from .deps import get_session, require_patient
from .schemas import DocumentUpload, NewRequest, PatientCaseView

router = APIRouter(prefix="/api/patient", tags=["patient"])
logger = logging.getLogger(__name__)


def _identity_verified(principal: Principal) -> bool:
    """The demo IdP's verdict for this patient (§18.3) - not something the client may send."""
    user = DEMO_USERS.get(principal.user_id)
    return bool(user and user.identity_verified)


@router.post("/requests", response_model=PatientCaseView, status_code=201)
def submit_request(body: NewRequest, principal: Principal = Depends(require_patient),
                   session: SessionService = Depends(get_session)) -> PatientCaseView:
    try:
        case_id = session.submit_request(principal.patient_id, body.text,
                                         identity_verified=_identity_verified(principal))
    except EventRejected as rejected:
        # The patient gets one code: a guard's reason (§3.1) is internal, and §12.3 keeps it
        # out of an answer a patient sees. The reason stays on the server - in the Blocked
        # audit row, and here in the application log (a code and a case id, nothing more).
        logger.info("request rejected: %s", rejected.reason)
        raise HTTPException(status_code=409, detail="request_rejected") from None
    return PatientCaseView.model_validate(session.patient_view(case_id))


@router.get("/requests", response_model=list[PatientCaseView])
def list_requests(principal: Principal = Depends(require_patient),
                  session: SessionService = Depends(get_session)) -> list[PatientCaseView]:
    return [PatientCaseView.model_validate(view) for view in session.cases_of(principal.patient_id)]


@router.get("/requests/{case_id}", response_model=PatientCaseView)
def get_request(case_id: str, principal: Principal = Depends(require_patient),
                session: SessionService = Depends(get_session)) -> PatientCaseView:
    case = _owned(session, principal, case_id)
    return PatientCaseView.model_validate(session.patient_view(case.case_id))


@router.post("/requests/{case_id}/documents", response_model=PatientCaseView)
def upload_document(case_id: str, body: DocumentUpload, principal: Principal = Depends(require_patient),
                    session: SessionService = Depends(get_session)) -> PatientCaseView:
    """200 with the case as it now stands - also when the upload was refused: the Session
    Service tombstones a rejected upload (D25) and the patient simply sees no change."""
    try:
        session.upload_document(principal.patient_id, case_id, body.document_id, body.content, fmt=body.format)
    except CaseNotFound:
        raise HTTPException(status_code=404, detail="case_not_found") from None
    return PatientCaseView.model_validate(session.patient_view(case_id))


def _owned(session: SessionService, principal: Principal, case_id: str):
    try:
        return session.case_for_patient(principal.patient_id, case_id)
    except CaseNotFound:
        raise HTTPException(status_code=404, detail="case_not_found") from None
