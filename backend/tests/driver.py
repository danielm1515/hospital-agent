"""The scripted demo components (hospital_agent.scripted) plus test-only shortcuts.

The shortcuts emit a component's event directly - without the real Tool Executor or
Readiness Check - so a test can put a case in an exact spot (a given document set, an
exhausted retry, a delivery that failed) and check one guard or rule there.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.engine import Engine

from hospital_agent import repository
from hospital_agent.case import ExecutionRecord
from hospital_agent.execution.gateway import MockGateway, ToolGateway
from hospital_agent.naming import Component, Event
from hospital_agent.scripted import PLAN, ScriptedAgents
from hospital_agent.state_manager import StateManager, TransitionResult

__all__ = ["PLAN", "Driver"]


class Driver(ScriptedAgents):
    def __init__(self, sm: StateManager, engine: Engine, patient_id: str = "P-10041",
                 gateway: ToolGateway | None = None) -> None:
        super().__init__(sm, engine, gateway or MockGateway(), patient_id)

    # --- direct emissions (no real Tool Executor / Readiness Check) ---------------------------

    def retrieved(self, **result) -> TransitionResult:
        return self._emit(Event.DATA_RETRIEVED, result, Component.TOOL_EXECUTOR)

    def transient_failure(self) -> TransitionResult:
        payload = {"idempotency_key": f"idem-{uuid.uuid4().hex[:8]}", "idempotent": True}
        return self._emit(Event.TOOL_TRANSIENT_FAILURE, payload, Component.TOOL_EXECUTOR)

    def retry_exhausted(self) -> TransitionResult:
        return self._emit(Event.RETRY_EXHAUSTED, {}, Component.TOOL_EXECUTOR)

    def deliver(self, status: str = "succeeded") -> TransitionResult:
        case = self.case
        execution_id = f"EXEC-{uuid.uuid4().hex[:8]}"
        with self.engine.begin() as conn:
            repository.insert_execution(conn, ExecutionRecord(
                execution_id=execution_id,
                case_id=case.case_id,
                patient_id=case.patient_id,
                action="SendStatusUpdate",
                step=case.current_step,
                retry_cycle=case.retry_cycle,
                attempt_number=1,
                idempotency_key=f"idem-{execution_id}",
                status=status,
            ))
        return self._emit(Event.CASE_RESOLVED, {"execution_id": execution_id}, Component.RESPONSE_DELIVERY)

    def missing_information(self, z3_result: str = "unsat") -> TransitionResult:
        payload = {"z3_result": z3_result, "patient_deadline": datetime.now(UTC) + timedelta(hours=96)}
        return self._emit(Event.MISSING_INFORMATION_DETECTED, payload, Component.READINESS_CHECK)

    def readiness_passed(self) -> TransitionResult:
        return self._emit(Event.READINESS_PASSED, {}, Component.READINESS_CHECK)

    # --- scenario prefixes -------------------------------------------------------------------

    def to_classified(self) -> None:
        self.submit()
        self.validate()
        self.classify()

    def retrieve_step(self, **result) -> None:
        """propose -> allow -> retrieved (direct), for the current plan step."""
        self.propose()
        self.allow()
        self.retrieved(**result)

    def to_assessing_readiness(self, required: list[str], held: list[str], hours_until: float = 96) -> None:
        self.to_classified()
        self.plan()
        self.retrieve_step(appointment_at=datetime.now(UTC) + timedelta(hours=hours_until))
        self.advance()
        self.retrieve_step(required_documents=required, held_documents=held)
        self.advance()
        self.retrieve_step()
