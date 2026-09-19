"""Scripted stand-ins for the components that do not exist yet (LLM design §1).

ScriptedAgents plays the Session Service (submitting a request, verifying identity,
uploading a document) and the human reviewers with fixed demo input. Everything else is
real: the Agent Orchestrator with the LLM components (hospital_agent.llm), the State
Manager, the Policy Service, the Readiness Check, the Temporal Monitor and the Tool
Executor. Each event comes from the component that owns it (§13.2), and the Session
Service keeps the request text and uploaded documents in the Data Log (§12.3).

Used by `python -m obs.golden` and, through tests/driver.py, by the tests. Sub-project 5
replaces it with the real Session Service and Human Review Service.
"""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sqlalchemy.engine import Engine

from . import data_log, repository
from .case import ApprovalRecord, CaseRecord
from .naming import Component, Event, State
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

    def _emit(self, event: Event, payload: dict, source: Component) -> TransitionResult:
        return self.sm.apply(self.case_id, event, payload, source)

    def _keep(self, kind: data_log.DataKind, content: str) -> None:
        with self.engine.begin() as conn:
            data_log.record(conn, self.case_id, self.patient_id, kind, content, self.sm.clock())

    # --- patient / Session Service ---------------------------------------------------------

    def submit(self) -> TransitionResult:
        result = self.sm.apply(None, Event.REQUEST_SUBMITTED, {"patient_id": self.patient_id}, Component.EXTERNAL)
        self.case_id = result.case_id
        return result

    def validate(self, text: str = REQUEST_TEXT, identity_verified: bool = True) -> TransitionResult:
        self._keep(data_log.DataKind.REQUEST_TEXT, text)
        return self._emit(Event.REQUEST_VALIDATED, {"text": text, "identity_verified": identity_verified},
                          Component.SESSION_SERVICE)

    def verification_failed(self) -> TransitionResult:
        return self._emit(Event.PATIENT_VERIFICATION_FAILED, {}, Component.SESSION_SERVICE)

    def upload(self, document_id: str, content: str = DOCUMENT_TEXT, **document) -> TransitionResult:
        self._keep(data_log.DataKind.UPLOADED_DOCUMENT, content)
        payload = {"document": {"document_id": document_id, "format": "pdf", "patient_id": self.patient_id, **document}}
        return self._emit(Event.DOCUMENT_UPLOADED, payload, Component.SESSION_SERVICE)

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
