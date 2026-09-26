"""Scripted stand-ins for the components that do not exist yet (LLM design §1).

ScriptedAgents plays the patient and the human reviewers with fixed demo input. The patient's
events go through the real Session Service (hospital_agent.session): it keeps the request
text and uploaded documents in the Data Log (§12.3), records a document before
DOCUMENT_UPLOADED with its content_hash on the event, and tombstones a rejected upload.
Everything else is real too: the Agent Orchestrator with the LLM components
(hospital_agent.llm), the State Manager, the Policy Service, the Readiness Check, the
Temporal Monitor and the Tool Executor. The reviewers' approvals are still inserted
directly here (approval / human), so a test can build an exact - also an invalid -
WorkflowDecision; the real path is hospital_agent.human_review.

Used by `python -m obs.golden` and, through tests/driver.py, by the tests.
"""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sqlalchemy.engine import Engine

from . import repository
from .case import ApprovalRecord, CaseRecord
from .naming import Component, Event, State
from .session import SessionService
from .state_manager import StateManager, TransitionResult

REQUEST_TEXT = "When is my appointment and which documents do I need?"
DOCUMENT_TEXT = "Blood test results: all values within the normal range."


class ScriptedAgents:
    def __init__(self, sm: StateManager, engine: Engine, patient_id: str = "P-10041") -> None:
        self.sm, self.engine, self.patient_id = sm, engine, patient_id
        self.case_id: str | None = None

    # --- reading ---------------------------------------------------------------------

    @property
    def case(self) -> CaseRecord:
        return self.sm.load(self.case_id)

    @property
    def state(self) -> State:
        return self.case.state

    def trace(self) -> list[repository.AuditEntry]:
        with self.engine.connect() as conn:
            return repository.load_trace(conn, self.case_id)

    @property
    def session(self) -> SessionService:
        """The Session Service on the current State Manager (a test may swap `sm`)."""
        return SessionService(self.sm)

    def _emit(self, event: Event, payload: dict, source: Component) -> TransitionResult:
        return self.sm.apply(self.case_id, event, payload, source)

    # --- patient, through the Session Service -------------------------------------------

    def submit(self, appointment_id: str | None = None) -> TransitionResult:
        result = self.session.open_case(self.patient_id, appointment_id=appointment_id)
        self.case_id = result.case_id
        return result

    def validate(self, text: str = REQUEST_TEXT, identity_verified: bool = True) -> TransitionResult:
        return self.session.validate_request(self.case_id, text, identity_verified=identity_verified)

    def verification_failed(self) -> TransitionResult:
        return self.session.verification_failed(self.case_id)

    def upload(self, document_id: str, content: str = DOCUMENT_TEXT, **document) -> TransitionResult:
        """`document` overrides the document's fields (e.g. a foreign patient_id, D25)."""
        return self.session.upload_document(self.patient_id, self.case_id, document_id, content,
                                            document_extra=document)

    # --- human reviewers -----------------------------------------------------------------

    def approval(self, decision: str, **fields) -> str:
        """Insert a WorkflowDecision for the current escalation and return its id."""
        case = self.case
        now = datetime.now(UTC)
        approval_id = f"APPR-{uuid.uuid4().hex[:6]}"
        record = ApprovalRecord(
            approval_id=approval_id,
            approval_type="WorkflowDecision",
            case_id=case.case_id,
            patient_id=case.patient_id,
            reviewer_id="coordinator_nurse",
            reviewer_role="clinical_staff",
            decision=decision,
            reason="reviewed by staff",
            shown_context_ref=f"ctx-{approval_id}",
            granted_at=now - timedelta(minutes=1),
            valid_until=now + timedelta(hours=1),
            escalation_kind=case.escalation_kind.value if case.escalation_kind else None,
        )
        with self.engine.begin() as conn:
            repository.insert_approval(conn, replace(record, **fields))
        return approval_id

    def human(self, event: Event, approval_id: str) -> TransitionResult:
        return self._emit(event, {"approval_id": approval_id}, Component.EXTERNAL)
