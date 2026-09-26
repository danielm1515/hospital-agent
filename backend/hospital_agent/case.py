"""Records that mirror the Postgres rows the Core reads (spec §18.2), and plan hashing.

The cases row is the ONLY place a case's current State lives (design §3): nothing
is kept in memory between requests, so a restart loses nothing.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .naming import Action, EscalationKind, SafetyLevel, State

# §0 scenario 3, §12.1: automatic attempts per step, per retry_cycle.
MAX_ATTEMPTS = 3


def compute_plan_hash(ordered_steps: list[dict[str, Any]]) -> str:
    """sha256 of canonical JSON (sorted keys, no spaces).

    Identical to OPA's crypto.sha256(json.marshal(ordered_steps)); reproduces the
    plan_hash of the spec §8 example input.
    """
    canonical = json.dumps(ordered_steps, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CaseRecord:
    """One row of the cases table."""

    case_id: str
    patient_id: str
    state: State
    state_version: int
    created_at: datetime
    updated_at: datetime
    intent: str | None = None
    safety_level: SafetyLevel | None = None
    identity_verified: bool = False
    plan_hash: str | None = None
    ordered_steps: list[dict[str, Any]] | None = None
    current_step: int | None = None
    retry_cycle: int = 0
    attempt_count: int = 0
    required_documents: list[str] | None = None
    held_documents: list[str] = field(default_factory=list)
    escalation_kind: EscalationKind | None = None
    escalated_from_state: State | None = None
    patient_deadline: datetime | None = None
    appointment_at: datetime | None = None  # from CheckAppointment's result (Execution design §5)
    # Sub-project 15 (design §5.2): set by PATIENT_REPLY_REQUESTED. human_engaged is never cleared -
    # a person has written to the patient, and the case never goes back to the agent.
    human_engaged: bool = False
    reply_kind: str | None = None  # "question" | "document" while AwaitingPatientReply
    requested_document: str | None = None  # the catalog type a document request asks for
    # Sub-project 18 (design D5/D6): the patient-chosen appointment - write-once, set only by
    # REQUEST_SUBMITTED's creation insert, never touched by RECORD_RETRIEVAL again (fix round 1,
    # I2). answered_appointment_id is the service's own echo of the appointment it resolved -
    # deliberately a separate column: the two must never be conflated, since the request always
    # sends appointment_id back, never answered_appointment_id.
    appointment_id: str | None = None
    answered_appointment_id: str | None = None
    department: str | None = None
    exam_type_label: str | None = None
    instruction_source_id: str | None = None
    instruction_version: str | None = None
    upcoming_count: int | None = None

    def step_action(self, step: int | None) -> Action | None:
        """The action at 1-based `step` of the approved plan, or None outside the plan."""
        if self.ordered_steps is None or step is None or not 1 <= step <= len(self.ordered_steps):
            return None
        return Action(self.ordered_steps[step - 1]["action"])

    @property
    def readiness_complete(self) -> bool:
        """Every required document is held - computed from tool results, never declared (§3.1)."""
        return self.required_documents is not None and set(self.required_documents) <= set(self.held_documents)

    @property
    def current_action(self) -> Action | None:
        return self.step_action(self.current_step)

    @property
    def next_action(self) -> Action | None:
        return None if self.current_step is None else self.step_action(self.current_step + 1)


def new_case(case_id: str, patient_id: str, now: datetime, *, appointment_id: str | None = None) -> CaseRecord:
    """The row REQUEST_SUBMITTED creates (§3 Initial -> Received).

    `appointment_id` (sub-project 18, D5): the patient's chosen appointment, when
    `POST /api/patient/requests` carried one - stored on the creation insert itself, not a
    later effect, since there is no case row to update before this one exists.
    """
    return CaseRecord(
        case_id=case_id,
        patient_id=patient_id,
        state=State.RECEIVED,
        state_version=1,
        created_at=now,
        updated_at=now,
        appointment_id=appointment_id,
    )


@dataclass(frozen=True)
class ApprovalRecord:
    """One row of the approvals table (§12.5)."""

    approval_id: str
    approval_type: str
    case_id: str
    patient_id: str
    reviewer_id: str
    reviewer_role: str
    decision: str
    reason: str
    shown_context_ref: str
    granted_at: datetime
    valid_until: datetime
    execution_id: str | None = None
    action: str | None = None
    content_hash: str | None = None
    escalation_kind: str | None = None
    plan_hash: str | None = None
    current_step: int | None = None
    verified_identity_ref: str | None = None
    patient_deadline: datetime | None = None
    consumed_at: datetime | None = None


@dataclass(frozen=True)
class ExecutionRecord:
    """One row of the executions table (§18.2). The Core only reads it (DeliveryConfirmed)."""

    execution_id: str
    case_id: str
    patient_id: str
    action: str
    step: int
    retry_cycle: int
    attempt_number: int
    idempotency_key: str
    status: str
    decision_token: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    # what the Policy decision was bound to (Execution design §3.1)
    state_version: int | None = None
    plan_hash: str | None = None
    approval_id: str | None = None
    content_hash: str | None = None
    medical_content_flag: bool = False
