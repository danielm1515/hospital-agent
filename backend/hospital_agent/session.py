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
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

from . import auth, data_log, document_status, naming, patient_messages, repository, upload_attempts
from .case import ApprovalRecord, CaseRecord, ExecutionRecord
from .document_intake import IntakeAnswer, IntakeUnavailable, sniff_kind
from .upload_attempts import UploadAttemptRecorder
from .documents import CATALOG_LABELS, document_label
from .llm.usage import SOURCE_DOCUMENT_SERVICE, UsageSink
from .naming import Component, Event, SafetyLevel, State
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

# Sub-project 17, Task 2 decision 4: a finer patient code for the document-service's own
# refusal `reason` (design §4.2), when it gave one this version recognises - but only on the
# result that reason is actually documented for (design §4.2's own table): `DOCUMENT_UNREADABLE`
# reasons never apply to a `DOCUMENT_EXPIRED` answer and vice versa, so a reason arriving on an
# unexpected result (a future document-service bug, or a result this version does not otherwise
# refine, e.g. PATIENT_MISMATCH) is ignored rather than misapplied. Everything else - no reason,
# a reason not listed here (parse_error, too_many_pages, too_much_text, classifier_unparsable),
# or a reason on the wrong result - falls back to `_REJECTION_CODES` above, i.e. stays
# "unreadable"/"expired" per the coarser per-result mapping.
_UNREADABLE_REASON_CODES = {
    "unknown_type": "unrecognised_type",
    "future_date": "bad_date",
    "no_text_layer": "unreadable_scan",
    "not_supported_format": "unsupported_format",
    "too_large": "too_large",
}
_EXPIRED_REASON_CODES = {
    "too_old": "expired",
    "no_date": "no_date",
}


def _rejection_code(answer: IntakeAnswer) -> str:
    """The patient-facing code for a document-service answer with no effective type (rule 6):
    the reason-based code when there is one this version knows for this exact result, else the
    coarser per-result code (fail closed, §14)."""
    if answer.reason is not None:
        by_result = (_UNREADABLE_REASON_CODES if answer.result == "DOCUMENT_UNREADABLE"
                    else _EXPIRED_REASON_CODES if answer.result == "DOCUMENT_EXPIRED"
                    else {})
        mapped = by_result.get(answer.reason)
        if mapped is not None:
            return mapped
    return _REJECTION_CODES.get(answer.result, _UNREADABLE)


class CaseNotFound(Exception):
    """An unknown case, or another patient's case - the API answers 404 for both."""


class NotWaitingForDocument(Exception):
    """A document for a case that is not in AwaitingPatientInput (the API answers 409)."""


class DocumentIntake(Protocol):
    def submit(self, patient_id: str, filename: str, data: bytes) -> IntakeAnswer: ...


@dataclass(frozen=True)
class UploadOutcome:
    """What the patient is told about one document upload: an abstract code and, when the document-service
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


@dataclass(frozen=True)
class PatientInstructions:
    """Sub-project 18 (design D11): exactly what was approved and shown - the case's own Data
    Log `instructions` entry, split on its first newline (`_keep_retrieved_content` writes it
    as `f"{title}\\n{text}"`, design D8)."""

    title: str
    text: str


MAX_REPLY_LENGTH = 2000
# The exact reference line reply_pdf writes (§9): a document-service id in the same shape
# document_intake._ID accepts (1-64 letters/digits/_/-, so a 1-character id is possible), a
# known catalog type, and the literal suffix. This is used only to *render* a reply already
# known to be a document reply (from what was asked, not from this shape) - never to decide
# whether it is one, since a patient's own text can share this exact shape by coincidence.
_DOCUMENT_REPLY = re.compile(r"^([A-Za-z0-9_-]{1,64}) ([A-Za-z0-9_-]{1,64}) ACCEPTED$")
_GENERIC_DOCUMENT_LABEL = "הועלה מסמך"  # a recognised reference line whose type this version does not know
_STAFF_MESSAGE_EVENTS = frozenset({Event.PATIENT_REPLY_REQUESTED.value, Event.HUMAN_RESOLVED_CASE.value,
                                   Event.HUMAN_REJECTED.value})
# Every text a document request can ever render (§7.1, §7.2: document_request is the only
# template a document request uses) - what _proven_question_hashes checks a request's staff
# message against, to prove a reply answers a question by what was asked.
_DOCUMENT_REQUEST_TEXTS = frozenset(
    patient_messages.render(patient_messages.DOCUMENT_REQUEST, doc_type) for doc_type in CATALOG_LABELS)


@dataclass(frozen=True)
class _Message:
    content_hash: str
    text: str
    at: datetime


def _approved_hashes(records: Iterable[ApprovalRecord]) -> set[str]:
    """content_hash of every consumed ContentApproval clinical_staff was granted for
    AnswerClinicalQuestion (§12.4) - shared by the clinical answer and the staff-message read,
    both of which trust exactly this approval, and nothing else, to authorise free text."""
    return {a.content_hash for a in records
            if a.approval_type == "ContentApproval" and a.reviewer_role == auth.CLINICAL_STAFF
            and a.consumed_at is not None and a.content_hash}


def _latest_content(entries: list[data_log.DataEntry]) -> str | None:
    """The newest entry's text that is still present (tombstoned ones skipped), or None."""
    texts = [e.content for e in entries if e.content is not None]
    return texts[-1] if texts else None


@dataclass(frozen=True)
class _CaseFacts:
    """Everything a patient view reads about one case: its trace, its Data Log by kind, the
    clinical approvals it trusts, and the executions its CASE_RESOLVED rows name. Loaded for a
    whole page of cases at once (_case_facts), so a list costs the same few statements however
    many cases it holds - each case used to cost about ten of its own."""

    trace: list[repository.AuditEntry]
    data: dict[data_log.DataKind, list[data_log.DataEntry]]
    approved: set[str]
    executions: dict[str, ExecutionRecord]

    def entries(self, kind: data_log.DataKind) -> list[data_log.DataEntry]:
        return self.data.get(kind, [])


def _case_facts(conn, case_ids: list[str]) -> dict[str, _CaseFacts]:
    """_CaseFacts for every case in four statements: traces, Data Log, approvals, executions."""
    traces = repository.load_traces(conn, case_ids)
    data = data_log.entries_for(conn, case_ids)
    approvals = repository.content_approvals_for_cases(conn, case_ids, naming.Action.ANSWER_CLINICAL_QUESTION.value)
    resolved_ids = {row.execution_id for trace in traces.values() for row in trace
                    if row.record_type == "Transition" and row.event == Event.CASE_RESOLVED.value
                    and row.execution_id}
    executions = repository.load_executions(conn, resolved_ids)
    return {case_id: _CaseFacts(traces.get(case_id, []), data.get(case_id, {}),
                                _approved_hashes(approvals.get(case_id, [])), executions)
            for case_id in case_ids}


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
    instructions: PatientInstructions | None = None  # sub-project 18 (design D11), only in "completed"


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
                 document_intake: DocumentIntake | None = None, usage_recorder: UsageSink | None = None,
                 upload_recorder: UploadAttemptRecorder | None = None) -> None:
        self.sm = state_manager
        self.engine = state_manager.engine
        self.wake = wake
        self.document_intake = document_intake
        # Sub-project 19 (design D5): where the document-service's LLM call is recorded; None
        # records nothing (the tests' default - the server passes its UsageRecorder).
        self.usage_recorder = usage_recorder
        self.upload_recorder = upload_recorder

    # --- events --------------------------------------------------------------------------

    def open_case(self, patient_id: str, *, appointment_id: str | None = None) -> TransitionResult:
        return self.sm.apply(None, Event.REQUEST_SUBMITTED,
                             {"patient_id": patient_id, "appointment_id": appointment_id}, Component.EXTERNAL)

    def validate_request(self, case_id: str, text: str, *, identity_verified: bool = True) -> TransitionResult:
        case = self._load(case_id)
        self._record(case, data_log.DataKind.REQUEST_TEXT, text)
        return self._validated(case_id, text, identity_verified)

    def verification_failed(self, case_id: str) -> TransitionResult:
        return self.sm.apply(case_id, Event.PATIENT_VERIFICATION_FAILED, {}, Component.SESSION_SERVICE)

    def submit_request(self, patient_id: str, text: str, *, identity_verified: bool,
                       appointment_id: str | None = None) -> str:
        """Open a case for the patient's request. An empty request is refused before anything
        is written: RequestValid (§3.1) would block it anyway, and an orphan case with an empty
        Data Log entry must not be left behind.

        `appointment_id` (sub-project 18, D5): the appointment the patient picked - rides on
        REQUEST_SUBMITTED and is stored on the case as it is created. The LLM never supplies
        it; CheckAppointment (§11) is the only later step allowed to read it back.
        """
        if not (text or "").strip():
            raise EventRejected(REQUEST_TEXT_REQUIRED)
        opened = _committed(self.open_case(patient_id, appointment_id=appointment_id))
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
        """Forward the patient's document (PDF, JPEG or PNG) to the document-service (sub-project 13, design §5.3; sub-project 17 task 2 for the image formats and reason).

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
             document_id=<the type>, content=<reference line>, fmt=<the uploaded file's own
             kind - pdf/jpg/png, sniffed by magic bytes, sub-project 17 task 2 decision 4>,
             document_extra={"document_ref": <DOC id>}) - the type is the document_id because
             HOLD_DOCUMENT appends document_id to held_documents; the reference line is exactly
             f"{document_ref} {document_type} ACCEPTED" (no medical text reaches the Data Log
             or the Safety re-check) - and return accepted.
          6. Without an effective type: NON_MEDICAL_DOCUMENT -> not_medical, PATIENT_MISMATCH ->
             not_yours, any other result (including a duplicate without duplicate_of) ->
             unreadable; DOCUMENT_UNREADABLE -> unreadable, refined to unrecognised_type,
             unreadable_scan, bad_date, unsupported_format or too_large when the
             document-service's own `reason` says so; DOCUMENT_EXPIRED -> expired, refined to
             no_date likewise (sub-project 17 task 2 decision 4 - a reason on any other result,
             or one this version does not know, is ignored, never misapplied). No event,
             nothing recorded.
          7. The application log gets only the outcome code, and - when the document-service
             gave one - its own reason code alongside it: never the patient id, the file name
             or a document id.

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
            self._record_attempt(case_id, upload_attempts.UPLOAD, "document_service_unavailable", str(unavailable))
            document_status.record_error(str(unavailable))
            raise
        document_status.record_ok()
        self._record_usage(case_id, answer)  # billed whatever the answer says next
        document_type, document_ref = _effective(answer)  # rule 4
        if document_type is None or document_ref is None:  # rule 6
            outcome = UploadOutcome(_rejection_code(answer), None)
        else:
            case = self._waiting_case(patient_id, case_id)  # rule 5, on the case as it is now
            if document_type not in (case.required_documents or []):
                outcome = UploadOutcome("not_required", document_type)
            elif document_type in case.held_documents:
                outcome = UploadOutcome("already_received", document_type)
            else:
                result = self.upload_document(patient_id, case_id, document_type,
                                              f"{document_ref} {document_type} ACCEPTED", fmt=_fmt_of(data),
                                              document_extra={"document_ref": document_ref})
                if not result.committed or result.state_after is not State.CLASSIFYING:
                    logger.info("pdf upload: not_waiting_for_document")
                    self._record_attempt(case_id, upload_attempts.UPLOAD, "not_waiting_for_document", answer.reason)
                    raise NotWaitingForDocument(case_id)
                outcome = UploadOutcome("accepted", document_type)
        _log_upload_outcome("pdf upload", outcome, answer.reason)  # rule 7: codes, nothing else
        self._record_attempt(case_id, upload_attempts.UPLOAD, outcome.code, answer.reason)
        return outcome

    def _record_attempt(self, case_id: str, kind: str, outcome: str, reason: str | None) -> None:
        """Row 98: the attempt and how it ended, for the staff journal - codes only, and never
        able to change the upload's answer (the recorder never raises)."""
        if self.upload_recorder is not None:
            self.upload_recorder.record(case_id, kind, outcome, reason)

    def _record_usage(self, case_id: str, answer: IntakeAnswer) -> None:
        """Sub-project 19 (design D5): the document-service's LLM call for this upload, against
        the case, as soon as it answered - the call was billed whether or not the document then
        counts, and even if the case moved on meanwhile. Outcome `ok`: an answer came back (a
        call that did not answer is a 503 with no usage to report). Bookkeeping only: nothing
        here can change the upload (the recorder never raises; this is the second fence)."""
        called = answer.llm_usage
        if called is None or self.usage_recorder is None:
            return
        try:
            self.usage_recorder.record(case_id, SOURCE_DOCUMENT_SERVICE, called.call, called.model, "ok", called.usage)
        except Exception as exc:
            logger.warning("llm_usage_write_failed error=%s", type(exc).__name__)

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
        if not body:
            raise EventRejected("reply_required")
        if len(body) > MAX_REPLY_LENGTH:
            raise EventRejected("reply_too_long")
        entry = self._record(case, data_log.DataKind.PATIENT_REPLY, body)
        return self._submit_reply(case_id, entry, {"reply_kind": "question"})

    def reply_pdf(self, patient_id: str, case_id: str, data: bytes, filename: str) -> UploadOutcome:
        """The requested document, through the sub-project 13 intake; it counts only when its type
        is the one asked for. The document stays in the document-service; the log gets only the code."""
        self._replying_case(patient_id, case_id, "document")
        if self.document_intake is None:
            raise IntakeUnavailable("not_configured")
        try:
            answer = self.document_intake.submit(patient_id, filename, data)
        except IntakeUnavailable as unavailable:
            logger.info("pdf reply: document_service_unavailable (%s)", unavailable)
            self._record_attempt(case_id, upload_attempts.REPLY, "document_service_unavailable", str(unavailable))
            document_status.record_error(str(unavailable))
            raise
        document_status.record_ok()
        self._record_usage(case_id, answer)
        document_type, document_ref = _effective(answer)
        if document_type is None or document_ref is None:
            outcome = UploadOutcome(_rejection_code(answer), None)
        else:
            case = self._replying_case(patient_id, case_id, "document")  # it may have moved meanwhile
            if document_type != case.requested_document:
                outcome = UploadOutcome("wrong_document_type", document_type)
            else:
                entry = self._record(case, data_log.DataKind.PATIENT_REPLY, f"{document_ref} {document_type} ACCEPTED")
                self._submit_reply(case_id, entry, {"reply_kind": "document", "document_type": document_type})
                outcome = UploadOutcome("accepted", document_type)
        _log_upload_outcome("pdf reply", outcome, answer.reason)
        self._record_attempt(case_id, upload_attempts.REPLY, outcome.code, answer.reason)
        return outcome

    def _replying_case(self, patient_id: str, case_id: str, kind: str) -> CaseRecord:
        case = self.case_for_patient(patient_id, case_id)
        if case.state is not State.AWAITING_PATIENT_REPLY:
            raise NotWaitingForReply(case_id)
        if case.reply_kind != kind:
            raise ReplyKindMismatch(case_id)
        return case

    def _submit_reply(self, case_id: str, entry: data_log.DataEntry, payload: dict) -> TransitionResult:
        """Apply PATIENT_REPLY_SUBMITTED; a reply that never committed must not leave the entry
        readable (§12.3) - the same fail-closed pattern as human_review._apply, on both exits:
        a blocked commit (a lost race - a concurrent reply, or the SLA timeout firing first -
        is `not_waiting_for_reply`, design §10, never a validation error) and a raising one
        (`sm.apply()` can also raise instead of returning a not-committed result)."""
        try:
            result = self.sm.apply(case_id, Event.PATIENT_REPLY_SUBMITTED,
                                   {**payload, "content_hash": entry.content_hash}, Component.SESSION_SERVICE)
        except Exception:
            self._tombstone_reply(entry.entry_id)
            raise
        if not result.committed:
            self._tombstone_reply(entry.entry_id)
            logger.info("reply: not_waiting_for_reply (%s)", result.reason)
            raise NotWaitingForReply(case_id)
        return result

    def _tombstone_reply(self, entry_id: str) -> None:
        with self.engine.begin() as conn:
            data_log.tombstone(conn, entry_id, self.sm.clock())

    # --- what the patient sees --------------------------------------------------------------

    def case_for_patient(self, patient_id: str, case_id: str) -> CaseRecord:
        case = self._load(case_id)
        if case.patient_id != patient_id:
            raise CaseNotFound(case_id)  # never reveal that another patient's case exists
        return case

    def patient_view(self, case_id: str) -> PatientView:
        case = self._load(case_id)
        with self.engine.connect() as conn:
            facts = _case_facts(conn, [case.case_id])
        return self._view(case, facts[case.case_id])

    def cases_of(self, patient_id: str, *, limit: int | None = None, offset: int = 0) -> list[PatientView]:
        """The patient's cases, newest first; one page when `limit` is given. Everything the
        views read is fetched for the whole page at once (_case_facts) - a fixed number of
        statements, not a handful per case."""
        with self.engine.connect() as conn:
            cases = repository.list_patient_cases(conn, patient_id, limit=limit, offset=offset)
            facts = _case_facts(conn, [case.case_id for case in cases])
        return [self._view(case, facts[case.case_id]) for case in cases]

    # --- helpers ---------------------------------------------------------------------------

    def _view(self, case: CaseRecord, facts: _CaseFacts) -> PatientView:
        trace = facts.trace
        resolved = [row for row in trace if row.record_type == "Transition"
                    and row.event == Event.CASE_RESOLVED.value]
        answer = self._clinical_answer(facts) if case.state is State.COMPLETED else None
        status = patient_status(case, delivered=bool(resolved) or answer is not None)
        history = status_history(trace, delivered=bool(resolved) or answer is not None)
        message = (self._delivered_message(facts, resolved[-1]) if resolved
                   else answer) if status == "completed" else None
        # Fix round 1 (I1/I2/M1/M2): instructions is non-null only for a case delivered by
        # the agent's own CASE_RESOLVED (never a clinical answer, and never any other
        # "completed"/"closed" shape), one that actually has a sub-project 18 instruction
        # source (never the pre-sub-project-18 shape), and whose safety_level was never
        # raised past MediumRisk by a later re-check - even if a human overrode a
        # PolicyReview escalation to let the case proceed regardless. `_instructions` itself
        # (below) is gate (d): the latest entry, present or nothing at all.
        instructions = (
            self._instructions(facts)
            if (status == "completed" and resolved and case.instruction_source_id is not None
                and case.safety_level in (SafetyLevel.LOW_RISK, SafetyLevel.MEDIUM_RISK))
            else None
        )
        staff = self._staff_messages(facts)
        replies = self._patient_replies(facts)
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
            request_text=_latest_content(facts.entries(data_log.DataKind.REQUEST_TEXT)),
            missing_document_ids=missing,
            missing_document_request_template_id=MISSING_DOCUMENT_TEMPLATE_ID if needs_document else None,
            message=message,
            history=history,
            document_upload="file" if self.document_intake is not None else "text",
            reply_request=reply_request,
            conversation=conversation,
            instructions=instructions,
        )

    @staticmethod
    def _delivered_message(facts: _CaseFacts, resolved: repository.AuditEntry) -> str | None:
        """The outgoing message the delivery confirmed by CASE_RESOLVED actually sent: the one
        whose hash is on that SendStatusUpdate execution. None if it cannot be tied to one."""
        execution = facts.executions.get(resolved.execution_id) if resolved.execution_id else None
        if execution is None or execution.content_hash is None:
            return None
        sent = [entry.content for entry in facts.entries(data_log.DataKind.OUTGOING_MESSAGE)
                if entry.content is not None and entry.content_hash == execution.content_hash]
        return sent[-1] if sent else None

    @staticmethod
    def _instructions(facts: _CaseFacts) -> PatientInstructions | None:
        """Sub-project 18 (design D11): the case's own Data Log `instructions` entry - exactly
        what was approved and shown (§12.3). The fixed plan loads instructions once (after the
        patient's upload, golden scenario 1 goes back through Classifying to AssessingReadiness,
        with no re-plan), but nothing in the Data Log limits a case to one such entry, so this is the
        LATEST entry by recency. Fix round 1 (I1/I2, gate d): if that latest entry is tombstoned, this is
        `None` - it never falls back to an earlier, still-present entry, since a deletion must
        hide the instructions text, not merely revert to a stale copy of it. `None` too when
        there is no entry at all.

        `_keep_retrieved_content` (execution/executor.py) writes the entry's content as
        `f"{title}\\n{text}"`; a title itself can never carry a newline (fix round 1, M5, the
        appointment-service gateway's own check), so the FIRST newline is always the real
        boundary. An entry with no newline at all (never produced by that writer today, but not
        assumed here) gets the generic title "הוראות הכנה" with the whole entry as its text,
        rather than swallowing the text into the title.
        """
        entries = facts.entries(data_log.DataKind.INSTRUCTIONS)
        if not entries or entries[-1].content is None:
            return None
        title, sep, text = entries[-1].content.partition("\n")
        if not sep:
            return PatientInstructions(title="הוראות הכנה", text=title)
        return PatientInstructions(title=title, text=text)

    def _clinical_answer(self, facts: _CaseFacts) -> str | None:
        """The answer a clinical_staff reviewer gave and approved (§5, §12.4), or None.

        The approval must be consumed - an approval that was never used authorised nothing -
        and its content_hash must match an outgoing message whose content is still there.
        This is what T6 does for the Tool Executor's path, applied where the patient reads.
        """
        approved = facts.approved
        if not approved:
            return None
        answers = [entry.content for entry
                   in facts.entries(data_log.DataKind.OUTGOING_MESSAGE)
                   if entry.content is not None and entry.content_hash in approved]
        return answers[-1] if answers else None

    def _staff_messages(self, facts: _CaseFacts) -> list[_Message]:
        """Staff messages the patient may read (design §7.5): the hash is on a committed request /
        resolve / reject row, and the text is a template's or has a consumed clinical approval."""
        committed = {r.content_hash for r in facts.trace
                     if r.record_type == "Transition" and r.content_hash and r.event in _STAFF_MESSAGE_EVENTS}
        approved = facts.approved
        return [_Message(e.content_hash, e.content, e.created_at)
                for e in facts.entries(data_log.DataKind.STAFF_MESSAGE)
                if e.content is not None and e.content_hash in committed
                and (patient_messages.is_template_text(e.content) or e.content_hash in approved)]

    def _patient_replies(self, facts: _CaseFacts) -> list[_Message]:
        """The patient's own replies whose PATIENT_REPLY_SUBMITTED committed. Fail-closed
        (design §7.5's read side, applied to this direction too): rather than assert a reply
        is a document reply from what was asked, this proves the opposite - a reply is a
        *proven question reply* only when the request before it is still readable and is not
        the document_request template - and shows it verbatim. Everything else (an unproven
        reply, because the request was deleted or never existed to check) falls to the
        reference-line shape: a document reply is shown as the document's label (or a generic
        one, for a type this version does not know), never its reference line; anything that
        does not match that shape is shown verbatim regardless. A §18.4 deletion of the
        request's own staff message can therefore never turn a genuine document reply's
        reference line into raw, readable text - it can only ever fall toward hiding it."""
        proven_question = self._proven_question_hashes(facts)
        committed = {r.content_hash for r in facts.trace if r.record_type == "Transition"
                     and r.event == Event.PATIENT_REPLY_SUBMITTED.value and r.content_hash}
        replies = []
        for e in facts.entries(data_log.DataKind.PATIENT_REPLY):
            if e.content is None or e.content_hash not in committed:
                continue
            if e.content_hash in proven_question:
                text = e.content  # proven: shown verbatim, whatever it looks like
            else:
                match = _DOCUMENT_REPLY.match(e.content)
                if match is None:
                    text = e.content  # not proven, and not shaped like a reference line either
                elif match.group(2) in CATALOG_LABELS:
                    text = f"הועלה המסמך: {document_label(match.group(2))}"
                else:
                    text = _GENERIC_DOCUMENT_LABEL  # a reference line, but of an unknown type
            replies.append(_Message(e.content_hash, text, e.created_at))
        return replies

    @staticmethod
    def _proven_question_hashes(facts: _CaseFacts) -> set[str]:
        """content_hash of every PATIENT_REPLY_SUBMITTED PROVEN to answer a question: the
        committed PATIENT_REPLY_REQUESTED immediately before it (read off the trace's own
        chronological order - only one request is ever open at a time) has a staff message
        that is still present (not tombstoned) and is not a document_request template text.
        Anything not provable this way - the message was deleted, or there was no preceding
        request at all - is left for _patient_replies' own reference-line check, never assumed
        to be a question just because it cannot be shown to be a document."""
        staff_text = {m.content_hash: m.content for m in
                     facts.entries(data_log.DataKind.STAFF_MESSAGE) if m.content is not None}
        pending: str | None = None
        hashes: set[str] = set()
        for row in facts.trace:
            if row.record_type != "Transition" or not row.content_hash:
                continue
            if row.event == Event.PATIENT_REPLY_REQUESTED.value:
                pending = row.content_hash
            elif row.event == Event.PATIENT_REPLY_SUBMITTED.value:
                text = staff_text.get(pending) if pending is not None else None
                if text is not None and text not in _DOCUMENT_REQUEST_TEXTS:
                    hashes.add(row.content_hash)
                pending = None
        return hashes

    def _request_text(self, case_id: str) -> str | None:
        with self.engine.connect() as conn:
            return _latest_content(data_log.entries(conn, case_id, data_log.DataKind.REQUEST_TEXT))

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


def _log_upload_outcome(verb: str, outcome: UploadOutcome, reason: str | None) -> None:
    """Sub-project 17, Task 2 decision 4: the code, and - when the document-service gave one -
    its real refusal reason too, both fixed codes, never content or a patient/file identifier
    (rule 7)."""
    if reason:
        logger.info("%s: %s (%s)", verb, outcome.code, reason)
    else:
        logger.info("%s: %s", verb, outcome.code)


# document_intake.sniff_kind's own vocabulary ("pdf"/"jpeg"/"png") to guards.SUPPORTED_DOCUMENT_FORMATS'
# ("pdf"/"jpg"/"png", the same set DocumentValid checks and the text upload's DOCUMENT_FORMATS
# already use) - sub-project 17 task 2 decision 4: the DOCUMENT_UPLOADED payload's "format" now
# names the file the patient actually sent, not a hard-coded "pdf".
_FMT_FOR_KIND = {"pdf": "pdf", "jpeg": "jpg", "png": "png"}


def _fmt_of(data: bytes) -> str:
    return _FMT_FOR_KIND[sniff_kind(data)]


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
