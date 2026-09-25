"""Session Service (spec §1, §12.3, §13.2, D24, D25; sub-project 5 design §4).

The patient's side of a case: submitting a request, the identity verdict, uploading a
document, and what the patient may see of the case.

Data Log discipline (§12.3):
  - the request text and an uploaded document are recorded BEFORE their event, since the
    Orchestrator may act the instant the event commits;
  - an upload's content_hash rides on DOCUMENT_UPLOADED, so the committed audit row names it;
  - an upload that did not move the case to Classifying (DocumentValid failed, or the event
    was blocked) is tombstoned at once - it must never sit in the Data Log readable.

The patient sees only an abstract status (design decision 8): never an escalation kind,
reasons or Audit. The ownership of every event (§13.2) is the one naming.EVENT_OWNER gives;
REQUEST_VALIDATED / PATIENT_VERIFICATION_FAILED come from the Session Service, and so does
DOCUMENT_UPLOADED - DocumentValid (§3.1) accepts a document only from it.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

from . import auth, data_log, naming, patient_messages, repository
from .case import CaseRecord
from .document_intake import IntakeAnswer, IntakeUnavailable
from .documents import CATALOG_LABELS, document_label
from .naming import Component, Event, State
from .state_manager import CaseNotFound as _UnknownCase
from .state_manager import StateManager, TransitionResult

MISSING_DOCUMENT_TEMPLATE_ID = "missing-document-v1"  # D24: a UI template, not an external call

REQUEST_TEXT_UNAVAILABLE = "request_text_unavailable"
REQUEST_TEXT_REQUIRED = "request_text_required"

logger = logging.getLogger(__name__)

# The document-service's rejections (design §4.1), as the patient-facing codes of design §5.3.
# Any result not here - including one this version does not know - is "unreadable" (fail closed).
_REJECTION_CODES = {
    "NON_MEDICAL_DOCUMENT": "not_medical",
    "DOCUMENT_UNREADABLE": "unreadable",
    "DOCUMENT_EXPIRED": "expired",
    "PATIENT_MISMATCH": "not_yours",
}
_UNREADABLE = "unreadable"


class CaseNotFound(Exception):
    """An unknown case, or another patient's case - the API answers 404 for both."""


class NotWaitingForDocument(Exception):
    """A PDF for a case that is not in AwaitingPatientInput (the API answers 409)."""


class DocumentIntake(Protocol):
    def submit(self, patient_id: str, filename: str, data: bytes) -> IntakeAnswer: ...


@dataclass(frozen=True)
class UploadOutcome:
    """What the patient is told about one PDF: an abstract code and, when the document-service
    named an accepted type, that type - never an escalation kind, a reason or an Audit row."""

    code: str
    document_type: str | None


class EventRejected(Exception):
    """A patient event was blocked (the API answers 409 {"detail": reason})."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class NotWaitingForReply(Exception):
    """Sub-project 15: the case is not waiting for a reply (API 409 "not_waiting_for_reply")."""


class ReplyKindMismatch(Exception):
    """Sub-project 15: a text for a document request, or a file for a question (409 "reply_kind_mismatch")."""


@dataclass(frozen=True)
class ReplyRequest:
    """What the staff asked for, while the case waits for the patient (design §10)."""

    kind: str  # "question" | "document"
    message: str | None
    document_type: str | None
    deadline: datetime | None


@dataclass(frozen=True)
class ConversationEntry:
    sender: str  # "staff" | "patient"
    text: str
    at: datetime


MAX_REPLY_LENGTH = 2000
# The Data Log line of an accepted document reply - the same shape sub-project 13 records.
_DOCUMENT_REPLY = re.compile(r"^(\S+) (\S+) ACCEPTED$")
_STAFF_MESSAGE_EVENTS = frozenset({Event.PATIENT_REPLY_REQUESTED.value, Event.HUMAN_RESOLVED_CASE.value,
                                   Event.HUMAN_REJECTED.value})


@dataclass(frozen=True)
class StatusChange:
    """One entry of the case's history: the abstract status, and when the case entered it."""

    status: str
    at: datetime


@dataclass(frozen=True)
class PatientView:
    case_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    request_text: str | None
    missing_document_ids: list[str]
    missing_document_request_template_id: str | None
    message: str | None
    history: list[StatusChange]
    document_upload: Literal["file", "text"] = "text"
    reply_request: ReplyRequest | None = None  # sub-project 15, while status is needs_reply
    conversation: list[ConversationEntry] = field(default_factory=list)  # staff messages and replies


_STATUS: dict[State, str] = {
    State.RECEIVED: "received",
    State.AWAITING_PATIENT_INPUT: "needs_document",
    State.AWAITING_HUMAN_REVIEW: "in_review",
    State.FAILED: "closed",
    State.AWAITING_PATIENT_REPLY: "needs_reply",  # sub-project 15
}


def abstract_status(state: State, delivered: bool) -> str:
    """The abstract status a patient sees for a state. `delivered`: the case has a committed
    CASE_RESOLVED row - a Completed case without one was closed by a reviewer, and nothing
    was sent."""
    if state is State.COMPLETED:
        return "completed" if delivered else "closed"
    return _STATUS.get(state, "in_progress")


def patient_status(case: CaseRecord, delivered: bool) -> str:
    return abstract_status(case.state, delivered)


def status_history(trace: list[repository.AuditEntry], delivered: bool) -> list[StatusChange]:
    """The case's abstract status over time, so the patient can see what happened and when.

    Only committed transitions carry a new state, and only the abstract status is exposed -
    the same six values `patient_status` returns, never a State, an event or a reason (§12.3).
    A transition that leaves the abstract status unchanged (Planning to Executing, say) adds
    nothing, so what is left is exactly the changes the patient could have noticed.
    """
    changes: list[StatusChange] = []
    for row in trace:
        if row.record_type != "Transition" or row.state_after is None:
            continue
        try:
            state = State(row.state_after)
        except ValueError:  # a state this version does not know: not the patient's problem
            continue
        status = abstract_status(state, delivered)
        if changes and changes[-1].status == status:
            continue
        changes.append(StatusChange(status=status, at=row.recorded_at))
    return changes


class SessionService:
    def __init__(self, state_manager: StateManager, *, wake: Callable[[], None] = lambda: None,
                 document_intake: DocumentIntake | None = None) -> None:
        self.sm = state_manager
        self.engine = state_manager.engine
        self.wake = wake
        self.document_intake = document_intake

    # --- events --------------------------------------------------------------------------

    def open_case(self, patient_id: str) -> TransitionResult:
        return self.sm.apply(None, Event.REQUEST_SUBMITTED, {"patient_id": patient_id}, Component.EXTERNAL)

    def validate_request(self, case_id: str, text: str, *, identity_verified: bool = True) -> TransitionResult:
        case = self._load(case_id)
        self._record(case, data_log.DataKind.REQUEST_TEXT, text)
        return self._validated(case_id, text, identity_verified)

    def verification_failed(self, case_id: str) -> TransitionResult:
        return self.sm.apply(case_id, Event.PATIENT_VERIFICATION_FAILED, {}, Component.SESSION_SERVICE)

    def submit_request(self, patient_id: str, text: str, *, identity_verified: bool) -> str:
        """Open a case for the patient's request. An empty request is refused before anything
        is written: RequestValid (§3.1) would block it anyway, and an orphan case with an empty
        Data Log entry must not be left behind."""
        if not (text or "").strip():
            raise EventRejected(REQUEST_TEXT_REQUIRED)
        opened = _committed(self.open_case(patient_id))
        case_id = opened.case_id
        if identity_verified:
            _committed(self.validate_request(case_id, text))
        else:
            self._record(self._load(case_id), data_log.DataKind.REQUEST_TEXT, text)
            _committed(self.verification_failed(case_id))
        self.wake()
        return case_id

    def revalidate(self, case_id: str) -> TransitionResult:
        """REQUEST_VALIDATED again from the stored request text, after a reviewer established the
        patient's identity (design decision 7). The text is already in the Data Log."""
        text = self._request_text(case_id)
        if text is None:
            raise EventRejected(REQUEST_TEXT_UNAVAILABLE)
        return _committed(self._validated(case_id, text, True))

    def upload_document(self, patient_id: str, case_id: str, document_id: str, content: str,
                        fmt: str = "pdf", document_extra: Mapping[str, Any] | None = None) -> TransitionResult:
        case = self.case_for_patient(patient_id, case_id)
        entry = self._record(case, data_log.DataKind.UPLOADED_DOCUMENT, content)
        document = {"document_id": document_id, "format": fmt, "patient_id": patient_id, **(document_extra or {})}
        payload = {"document": document, "content_hash": entry.content_hash}
        result = self.sm.apply(case_id, Event.DOCUMENT_UPLOADED, payload, Component.SESSION_SERVICE)
        if not result.committed or result.state_after is not State.CLASSIFYING:
            # §12.3 privacy: a rejected upload (guard_failed self-loop, or Blocked) must not stay
            # readable. Nothing reads it anyway: its hash is on no accepted row (D25).
            with self.engine.begin() as conn:
                data_log.tombstone(conn, entry.entry_id, self.sm.clock())
        self.wake()
        return result

    def upload_pdf(self, patient_id: str, case_id: str, data: bytes, filename: str) -> UploadOutcome:
        """Forward the patient's PDF to the document-service (sub-project 13, design §5.3).

        The rules:
          1. The case must be the patient's (CaseNotFound -> 404) and in AwaitingPatientInput
             (else NotWaitingForDocument -> 409 not_waiting_for_document) - checked before
             anything is sent to the document-service.
          2. No intake client configured -> the route answers 404 file_upload_not_enabled (the
             text upload stays the way in).
          3. Forward (patient_id, filename, data); IntakeUnavailable (no answer, 5xx, 401, or a
             body that is not the contract) -> 503 document_service_unavailable; nothing recorded.
          4. The effective type: ACCEPTED -> its document_type; DUPLICATE_DOCUMENT with
             duplicate_of and a document_type -> that type (a retry after a timeout is the
             document already delivered, design §4.1).
          5. With an effective type: if it is not in case.required_documents -> not_required, no
             event; if it is already in case.held_documents -> already_received, no event;
             otherwise call the existing upload_document(patient_id, case_id,
             document_id=<the type>, content=<reference line>, fmt="pdf",
             document_extra={"document_ref": <DOC id>}) - the type is the document_id because
             HOLD_DOCUMENT appends document_id to held_documents; the reference line is exactly
             f"{document_ref} {document_type} ACCEPTED" (no medical text reaches the Data Log
             or the Safety re-check) - and return accepted.
          6. Without an effective type: NON_MEDICAL_DOCUMENT -> not_medical, DOCUMENT_UNREADABLE
             -> unreadable, DOCUMENT_EXPIRED -> expired, PATIENT_MISMATCH -> not_yours, any
             other result (including a duplicate without duplicate_of) -> unreadable. No event,
             nothing recorded.
          7. The application log gets only the outcome code - never the patient id, the file
             name or a document id.

        The document-service may take up to 70 s, so rule 5 reads the case again once it has
        answered: a case that moved on meanwhile is NotWaitingForDocument, and so is one whose
        DOCUMENT_UPLOADED did not reach Classifying (upload_document has tombstoned the
        reference line by then). For a duplicate, <DOC id> is duplicate_of - the original,
        accepted document, which is the one the reference line names.
        """
        self._waiting_case(patient_id, case_id)  # rule 1, before anything leaves
        if self.document_intake is None:
            raise IntakeUnavailable("not_configured")  # rule 2 is the route's; never reached from it
        try:
            answer = self.document_intake.submit(patient_id, filename, data)  # rule 3
        except IntakeUnavailable as unavailable:
            # The client's code only (no_answer, status_<n>, invalid_response), so an operator
            # can tell a 400 from a 5xx - never a patient id, a file name or a document id.
            logger.info("pdf upload: document_service_unavailable (%s)", unavailable)
            raise
        document_type, document_ref = _effective(answer)  # rule 4
        if document_type is None or document_ref is None:  # rule 6
            outcome = UploadOutcome(_REJECTION_CODES.get(answer.result, _UNREADABLE), None)
        else:
            case = self._waiting_case(patient_id, case_id)  # rule 5, on the case as it is now
            if document_type not in (case.required_documents or []):
                outcome = UploadOutcome("not_required", document_type)
            elif document_type in case.held_documents:
                outcome = UploadOutcome("already_received", document_type)
            else:
                result = self.upload_document(patient_id, case_id, document_type,
                                              f"{document_ref} {document_type} ACCEPTED", fmt="pdf",
                                              document_extra={"document_ref": document_ref})
                if not result.committed or result.state_after is not State.CLASSIFYING:
                    logger.info("pdf upload: not_waiting_for_document")
                    raise NotWaitingForDocument(case_id)
                outcome = UploadOutcome("accepted", document_type)
        logger.info("pdf upload: %s", outcome.code)  # rule 7: the code, nothing else
        return outcome

    def _waiting_case(self, patient_id: str, case_id: str) -> CaseRecord:
        case = self.case_for_patient(patient_id, case_id)
        if case.state is not State.AWAITING_PATIENT_INPUT:
            logger.info("pdf upload: not_waiting_for_document")
            raise NotWaitingForDocument(case_id)
        return case

    # --- sub-project 15: the patient's reply to a staff request (design §9) ----------------

    def reply_text(self, patient_id: str, case_id: str, text: str) -> TransitionResult:
        case = self._replying_case(patient_id, case_id, "question")
        body = (text or "").strip()
        if not body or len(body) > MAX_REPLY_LENGTH:
            raise EventRejected("reply_required")
        entry = self._record(case, data_log.DataKind.PATIENT_REPLY, body)
        return self._submit_reply(case_id, entry, {"reply_kind": "question"})

    def reply_pdf(self, patient_id: str, case_id: str, data: bytes, filename: str) -> UploadOutcome:
        """The requested document, through the sub-project 13 intake; it counts only when its type
        is the one asked for. The PDF stays in the document-service; the log gets only the code."""
        self._replying_case(patient_id, case_id, "document")
        if self.document_intake is None:
            raise IntakeUnavailable("not_configured")
        try:
            answer = self.document_intake.submit(patient_id, filename, data)
        except IntakeUnavailable as unavailable:
            logger.info("pdf reply: document_service_unavailable (%s)", unavailable)
            raise
        document_type, document_ref = _effective(answer)
        if document_type is None or document_ref is None:
            outcome = UploadOutcome(_REJECTION_CODES.get(answer.result, _UNREADABLE), None)
        else:
            case = self._replying_case(patient_id, case_id, "document")  # it may have moved meanwhile
            if document_type != case.requested_document:
                outcome = UploadOutcome("wrong_document_type", document_type)
            else:
                entry = self._record(case, data_log.DataKind.PATIENT_REPLY, f"{document_ref} {document_type} ACCEPTED")
                try:
                    self._submit_reply(case_id, entry, {"reply_kind": "document", "document_type": document_type})
                except EventRejected:
                    logger.info("pdf reply: not_waiting_for_reply")
                    raise NotWaitingForReply(case_id) from None
                outcome = UploadOutcome("accepted", document_type)
        logger.info("pdf reply: %s", outcome.code)
        return outcome

    def _replying_case(self, patient_id: str, case_id: str, kind: str) -> CaseRecord:
        case = self.case_for_patient(patient_id, case_id)
        if case.state is not State.AWAITING_PATIENT_REPLY:
            raise NotWaitingForReply(case_id)
        if case.reply_kind != kind:
            raise ReplyKindMismatch(case_id)
        return case

    def _submit_reply(self, case_id: str, entry: data_log.DataEntry, payload: dict) -> TransitionResult:
        result = self.sm.apply(case_id, Event.PATIENT_REPLY_SUBMITTED, {**payload, "content_hash": entry.content_hash},
                               Component.SESSION_SERVICE)
        if not result.committed:
            with self.engine.begin() as conn:  # §12.3: a refused reply must not stay readable
                data_log.tombstone(conn, entry.entry_id, self.sm.clock())
            raise EventRejected(result.reason or "blocked")
        return result

    # --- what the patient sees --------------------------------------------------------------

    def case_for_patient(self, patient_id: str, case_id: str) -> CaseRecord:
        case = self._load(case_id)
        if case.patient_id != patient_id:
            raise CaseNotFound(case_id)  # never reveal that another patient's case exists
        return case

    def patient_view(self, case_id: str) -> PatientView:
        return self._view(self._load(case_id))

    def cases_of(self, patient_id: str) -> list[PatientView]:
        with self.engine.connect() as conn:
            cases = repository.list_patient_cases(conn, patient_id)
        return [self._view(case) for case in cases]

    # --- helpers ---------------------------------------------------------------------------

    def _view(self, case: CaseRecord) -> PatientView:
        with self.engine.connect() as conn:
            trace = repository.load_trace(conn, case.case_id)
            resolved = [row for row in trace if row.record_type == "Transition"
                        and row.event == Event.CASE_RESOLVED.value]
            answer = self._clinical_answer(conn, case.case_id) if case.state is State.COMPLETED else None
            status = patient_status(case, delivered=bool(resolved) or answer is not None)
            history = status_history(trace, delivered=bool(resolved) or answer is not None)
            message = (self._delivered_message(conn, case.case_id, resolved[-1]) if resolved
                       else answer) if status == "completed" else None
            staff = self._staff_messages(conn, case.case_id, trace)
            replies = self._patient_replies(conn, case.case_id, trace)
        needs_document = status == "needs_document"
        missing = sorted(set(case.required_documents or []) - set(case.held_documents)) if needs_document else []
        committed = [r for r in trace if r.record_type == "Transition"]
        reply_request = None
        if case.state is State.AWAITING_PATIENT_REPLY:
            asked = [r.content_hash for r in committed if r.event == Event.PATIENT_REPLY_REQUESTED.value]
            text = next((m.text for m in reversed(staff) if asked and m.content_hash == asked[-1]), None)
            reply_request = ReplyRequest(case.reply_kind, text, case.requested_document, case.patient_deadline)
        if status == "closed" and message is None:
            closing = [r.content_hash for r in committed if r.content_hash and r.event in
                       (Event.HUMAN_RESOLVED_CASE.value, Event.HUMAN_REJECTED.value)]
            message = next((m.text for m in reversed(staff) if closing and m.content_hash == closing[-1]), None)
        conversation = sorted([ConversationEntry("staff", m.text, m.at) for m in staff]
                              + [ConversationEntry("patient", r.text, r.at) for r in replies], key=lambda e: e.at)
        return PatientView(
            case_id=case.case_id,
            status=status,
            created_at=case.created_at,
            updated_at=case.updated_at,
            request_text=self._request_text(case.case_id),
            missing_document_ids=missing,
            missing_document_request_template_id=MISSING_DOCUMENT_TEMPLATE_ID if needs_document else None,
            message=message,
            history=history,
            document_upload="file" if self.document_intake is not None else "text",
            reply_request=reply_request,
            conversation=conversation,
        )

    @staticmethod
    def _delivered_message(conn, case_id: str, resolved: repository.AuditEntry) -> str | None:
        """The outgoing message the delivery confirmed by CASE_RESOLVED actually sent: the one
        whose hash is on that SendStatusUpdate execution. None if it cannot be tied to one."""
        execution = repository.load_execution(conn, resolved.execution_id) if resolved.execution_id else None
        if execution is None or execution.content_hash is None:
            return None
        sent = [entry.content for entry in data_log.entries(conn, case_id, data_log.DataKind.OUTGOING_MESSAGE)
                if entry.content is not None and entry.content_hash == execution.content_hash]
        return sent[-1] if sent else None

    def _clinical_answer(self, conn, case_id: str) -> str | None:
        """The answer a clinical_staff reviewer gave and approved (§5, §12.4), or None.

        The approval must be consumed - an approval that was never used authorised nothing -
        and its content_hash must match an outgoing message whose content is still there.
        This is what T6 does for the Tool Executor's path, applied where the patient reads.
        """
        approved = {
            approval.content_hash
            for approval in repository.content_approvals_for(
                conn, case_id, naming.Action.ANSWER_CLINICAL_QUESTION.value)
            if approval.approval_type == "ContentApproval"
            and approval.reviewer_role == auth.CLINICAL_STAFF
            and approval.consumed_at is not None
            and approval.content_hash
        }
        if not approved:
            return None
        answers = [entry.content for entry
                   in data_log.entries(conn, case_id, data_log.DataKind.OUTGOING_MESSAGE)
                   if entry.content is not None and entry.content_hash in approved]
        return answers[-1] if answers else None

    @dataclass(frozen=True)
    class _Message:
        content_hash: str
        text: str
        at: datetime

    def _staff_messages(self, conn, case_id: str, trace) -> list[_Message]:
        """Staff messages the patient may read (design §7.5): the hash is on a committed request /
        resolve / reject row, and the text is a template's or has a consumed clinical approval."""
        committed = {r.content_hash for r in trace
                     if r.record_type == "Transition" and r.content_hash and r.event in _STAFF_MESSAGE_EVENTS}
        approved = {a.content_hash for a in repository.content_approvals_for(
                        conn, case_id, naming.Action.ANSWER_CLINICAL_QUESTION.value)
                    if a.approval_type == "ContentApproval" and a.reviewer_role == auth.CLINICAL_STAFF
                    and a.consumed_at is not None and a.content_hash}
        return [self._Message(e.content_hash, e.content, e.created_at)
                for e in data_log.entries(conn, case_id, data_log.DataKind.STAFF_MESSAGE)
                if e.content is not None and e.content_hash in committed
                and (patient_messages.is_template_text(e.content) or e.content_hash in approved)]

    def _patient_replies(self, conn, case_id: str, trace) -> list[_Message]:
        """The patient's own replies whose PATIENT_REPLY_SUBMITTED committed; a document reply is
        shown as the document's label, never its reference line."""
        committed = {r.content_hash for r in trace if r.record_type == "Transition"
                     and r.event == Event.PATIENT_REPLY_SUBMITTED.value and r.content_hash}
        replies = []
        for e in data_log.entries(conn, case_id, data_log.DataKind.PATIENT_REPLY):
            if e.content is None or e.content_hash not in committed:
                continue
            match = _DOCUMENT_REPLY.match(e.content)
            text = (f"הועלה המסמך: {document_label(match.group(2))}"
                    if match and match.group(2) in CATALOG_LABELS else e.content)
            replies.append(self._Message(e.content_hash, text, e.created_at))
        return replies

    def _request_text(self, case_id: str) -> str | None:
        with self.engine.connect() as conn:
            texts = [e.content for e in data_log.entries(conn, case_id, data_log.DataKind.REQUEST_TEXT)
                     if e.content is not None]
        return texts[-1] if texts else None

    def _validated(self, case_id: str, text: str, identity_verified: bool) -> TransitionResult:
        return self.sm.apply(case_id, Event.REQUEST_VALIDATED, {"text": text, "identity_verified": identity_verified},
                             Component.SESSION_SERVICE)

    def _record(self, case: CaseRecord, kind: data_log.DataKind, content: str) -> data_log.DataEntry:
        with self.engine.begin() as conn:
            return data_log.record(conn, case.case_id, case.patient_id, kind, content, self.sm.clock())

    def _load(self, case_id: str) -> CaseRecord:
        try:
            return self.sm.load(case_id)
        except _UnknownCase:
            raise CaseNotFound(case_id) from None


def _effective(answer: IntakeAnswer) -> tuple[str | None, str | None]:
    """Rule 4: (the accepted document's type, its id), or (None, None)."""
    if answer.result == "ACCEPTED" and answer.document_type and answer.document_id:
        return answer.document_type, answer.document_id
    if answer.result == "DUPLICATE_DOCUMENT" and answer.duplicate_of and answer.document_type:
        return answer.document_type, answer.duplicate_of
    return None, None


def _committed(result: TransitionResult) -> TransitionResult:
    if not result.committed:
        raise EventRejected(result.reason or "blocked")
    return result
