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

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from . import data_log, repository
from .case import CaseRecord
from .naming import Component, Event, State
from .state_manager import CaseNotFound as _UnknownCase
from .state_manager import StateManager, TransitionResult

MISSING_DOCUMENT_TEMPLATE_ID = "missing-document-v1"  # D24: a UI template, not an external call

REQUEST_TEXT_UNAVAILABLE = "request_text_unavailable"


class CaseNotFound(Exception):
    """An unknown case, or another patient's case - the API answers 404 for both."""


class EventRejected(Exception):
    """A patient event was blocked (the API answers 409 {"detail": reason})."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


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


_STATUS: dict[State, str] = {
    State.RECEIVED: "received",
    State.AWAITING_PATIENT_INPUT: "needs_document",
    State.AWAITING_HUMAN_REVIEW: "in_review",
    State.FAILED: "closed",
}


def patient_status(case: CaseRecord, delivered: bool) -> str:
    """The abstract status a patient sees. `delivered`: the case has a committed CASE_RESOLVED
    row - a Completed case without one was closed by a reviewer, and nothing was sent."""
    if case.state is State.COMPLETED:
        return "completed" if delivered else "closed"
    return _STATUS.get(case.state, "in_progress")


class SessionService:
    def __init__(self, state_manager: StateManager, *, wake: Callable[[], None] = lambda: None) -> None:
        self.sm = state_manager
        self.engine = state_manager.engine
        self.wake = wake

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
            status = patient_status(case, delivered=bool(resolved))
            message = self._delivered_message(conn, case.case_id, resolved[-1]) if status == "completed" else None
        needs_document = status == "needs_document"
        missing = sorted(set(case.required_documents or []) - set(case.held_documents)) if needs_document else []
        return PatientView(
            case_id=case.case_id,
            status=status,
            created_at=case.created_at,
            updated_at=case.updated_at,
            request_text=self._request_text(case.case_id),
            missing_document_ids=missing,
            missing_document_request_template_id=MISSING_DOCUMENT_TEMPLATE_ID if needs_document else None,
            message=message,
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


def _committed(result: TransitionResult) -> TransitionResult:
    if not result.committed:
        raise EventRejected(result.reason or "blocked")
    return result
