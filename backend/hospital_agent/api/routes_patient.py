"""/api/patient: the patient screen's API (§1, §12.3, D24; design §4, §6).

Every route works on the patient the token names. A case that belongs to someone else is
404, exactly like a case that does not exist - the API never reveals that it exists. The
answer is always the abstract PatientCaseView: no escalation kind, no reason, no Audit.
"""
from __future__ import annotations

import logging
from email.message import Message
from email.parser import HeaderParser

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from ..auth import DEMO_USERS, Principal
from ..document_intake import IntakeUnavailable
from ..session import (
    CaseNotFound,
    EventRejected,
    NotWaitingForDocument,
    NotWaitingForReply,
    ReplyKindMismatch,
    SessionService,
)
from .deps import get_session, require_patient
from .schemas import DocumentUpload, NewRequest, PatientCaseView, PdfUploadResponse, ReplyBody, UploadResult

router = APIRouter(prefix="/api/patient", tags=["patient"])
logger = logging.getLogger(__name__)

MAX_PDF_BYTES = 10 * 1024 * 1024  # the document-service's own limit (design §5.3)
UPLOAD_BODY_LIMIT = MAX_PDF_BYTES + 64 * 1024  # the file plus the multipart framing around it
MAX_FORM_PARTS = 64  # the form has one file part; a few others are ignored, thousands are refused


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
        # audit row, and here in the application log (just the code, nothing more).
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
    Service tombstones a rejected upload (D25) and the patient simply sees no change.

    Refused with `409 use_file_upload` once a document-service is configured
    (`session.document_intake is not None`): the real upload (`POST .../documents/file`) then
    runs the document-service's intake, and this route would otherwise let arbitrary text be
    recorded as a held document without it. Without a document-service this route behaves
    exactly as before (`document_upload` stays `"text"`)."""
    if session.document_intake is not None:
        raise HTTPException(status_code=409, detail="use_file_upload")
    try:
        session.upload_document(principal.patient_id, case_id, body.document_id, body.content, fmt=body.format)
    except CaseNotFound:
        raise HTTPException(status_code=404, detail="case_not_found") from None
    return PatientCaseView.model_validate(session.patient_view(case_id))


@router.post("/requests/{case_id}/documents/file", response_model=PdfUploadResponse)
async def upload_pdf(case_id: str, request: Request, principal: Principal = Depends(require_patient),
                     session: SessionService = Depends(get_session)) -> PdfUploadResponse:
    """Sub-project 13 (design §5.3): the patient's PDF (multipart, one part named `file`),
    forwarded to the document-service and never stored here. 200 with the outcome code and the
    case as it now stands - also for a rejected or not-required document, which moves nothing.

    The multipart body is parsed with the standard library (`_file_part`), not FastAPI's
    `UploadFile`, which needs python-multipart - a runtime dependency this project does not
    take. The UploadSizeLimit middleware has already refused a body over UPLOAD_BODY_LIMIT by
    its Content-Length; the read below is capped at the same limit all the same.
    """
    if session.document_intake is None:
        raise HTTPException(status_code=404, detail="file_upload_not_enabled")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > UPLOAD_BODY_LIMIT:
            raise HTTPException(status_code=413, detail="too_large")
    # Off the event loop: up to 10 MB of parsing must not stall every other request.
    part = await run_in_threadpool(_file_part, request.headers.get("content-type", ""), bytes(body))
    if part is None:
        raise HTTPException(status_code=422, detail="invalid_body")
    filename, data = part
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="too_large")
    try:
        # Off the event loop: the document-service may take up to 70 s to answer.
        outcome = await run_in_threadpool(session.upload_pdf, principal.patient_id, case_id, data, filename)
        view = await run_in_threadpool(session.patient_view, case_id)
    except CaseNotFound:
        raise HTTPException(status_code=404, detail="case_not_found") from None
    except NotWaitingForDocument:
        raise HTTPException(status_code=409, detail="not_waiting_for_document") from None
    except IntakeUnavailable:
        raise HTTPException(status_code=503, detail="document_service_unavailable") from None
    return PdfUploadResponse(upload=UploadResult.model_validate(outcome),
                             request=PatientCaseView.model_validate(view))


@router.post("/requests/{case_id}/reply", response_model=PatientCaseView)
def reply(case_id: str, body: ReplyBody, principal: Principal = Depends(require_patient),
          session: SessionService = Depends(get_session)) -> PatientCaseView:
    """Sub-project 15 (design §9): the patient's text answer to a staff question."""
    try:
        session.reply_text(principal.patient_id, case_id, body.text)
    except CaseNotFound:
        raise HTTPException(status_code=404, detail="case_not_found") from None
    except NotWaitingForReply:
        raise HTTPException(status_code=409, detail="not_waiting_for_reply") from None
    except ReplyKindMismatch:
        raise HTTPException(status_code=409, detail="reply_kind_mismatch") from None
    except EventRejected as rejected:
        # reply_too_long (ReplyBody already caps text at 2000, a 422 invalid_body; this is
        # defence for a length the service counts differently) is a form error, not a state
        # refusal - answered like the schema's own 422, never the generic 409.
        if rejected.reason == "reply_too_long":
            raise HTTPException(status_code=422, detail="reply_too_long") from None
        raise HTTPException(status_code=409, detail=rejected.reason) from None
    return PatientCaseView.model_validate(session.patient_view(case_id))


@router.post("/requests/{case_id}/reply/file", response_model=PdfUploadResponse)
async def reply_pdf(case_id: str, request: Request, principal: Principal = Depends(require_patient),
                    session: SessionService = Depends(get_session)) -> PdfUploadResponse:
    """Sub-project 15 (design §9): the requested document, as the sub-project 13 upload does it."""
    if session.document_intake is None:
        raise HTTPException(status_code=404, detail="file_upload_not_enabled")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > UPLOAD_BODY_LIMIT:
            raise HTTPException(status_code=413, detail="too_large")
    part = await run_in_threadpool(_file_part, request.headers.get("content-type", ""), bytes(body))
    if part is None:
        raise HTTPException(status_code=422, detail="invalid_body")
    filename, data = part
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="too_large")
    try:
        outcome = await run_in_threadpool(session.reply_pdf, principal.patient_id, case_id, data, filename)
        view = await run_in_threadpool(session.patient_view, case_id)
    except CaseNotFound:
        raise HTTPException(status_code=404, detail="case_not_found") from None
    except NotWaitingForReply:
        raise HTTPException(status_code=409, detail="not_waiting_for_reply") from None
    except ReplyKindMismatch:
        raise HTTPException(status_code=409, detail="reply_kind_mismatch") from None
    except IntakeUnavailable:
        raise HTTPException(status_code=503, detail="document_service_unavailable") from None
    return PdfUploadResponse(upload=UploadResult.model_validate(outcome),
                             request=PatientCaseView.model_validate(view))


def _file_part(content_type: str, body: bytes) -> tuple[str, bytes] | None:
    """(filename, bytes) of the first multipart/form-data part named `file` that carries a
    filename, or None when the body is not such a form. Strict and exact for binary content:
    parts are split on CRLF + "--" + boundary, as RFC 7578 / RFC 2046 define them. At most
    MAX_FORM_PARTS parts are looked at - a body with more is not the one-file form this route
    takes - and any failure while parsing is None (422), never a 500."""
    try:
        return _parse_file_part(content_type, body)
    except Exception:  # noqa: BLE001 - a hostile body must end in 422, whatever it trips
        return None


def _parse_file_part(content_type: str, body: bytes) -> tuple[str, bytes] | None:
    header = Message()
    header["content-type"] = content_type
    boundary = header.get_param("boundary")
    if header.get_content_type() != "multipart/form-data" or not isinstance(boundary, str) \
            or not 1 <= len(boundary) <= 70:
        return None
    delimiter = b"\r\n--" + boundary.encode("latin-1", "replace")
    # preamble + at most MAX_FORM_PARTS parts + the closing "--": a longer body leaves its rest
    # unsplit in the last piece, which then does not start with "--" and is refused.
    sections = (b"\r\n" + body).split(delimiter, MAX_FORM_PARTS + 1)
    if len(sections) < 3 or not sections[-1].startswith(b"--"):
        return None  # no part, too many parts, or no closing delimiter
    for section in sections[1:-1]:
        if not section.startswith(b"\r\n"):
            return None
        pieces = section[2:].split(b"\r\n\r\n", 1)
        if len(pieces) != 2 or not pieces[0].strip():
            continue  # a part without header lines is not the file
        raw_headers, content = pieces
        headers = HeaderParser().parsestr(raw_headers.decode("utf-8", "replace") + "\r\n\r\n")
        disposition = Message()
        disposition["content-disposition"] = headers.get("content-disposition", "")
        name = disposition.get_param("name", header="content-disposition")
        filename = disposition.get_filename()
        if disposition.get_content_disposition() == "form-data" and name == "file" and filename is not None:
            return filename, content
    return None


def _owned(session: SessionService, principal: Principal, case_id: str):
    try:
        return session.case_for_patient(principal.patient_id, case_id)
    except CaseNotFound:
        raise HTTPException(status_code=404, detail="case_not_found") from None
