"""The external systems the Tool Executor calls, and their demo mocks (Execution design §4).

Only the fields a target may receive are sent (spec §11 minimized_fields): the
parameters of each action are fixed here, and a test checks them against the
Datalog export. The mocks return the demo data of Execution design decision 5 and
can be scripted to time out or fail, so the three scenarios and the D-tests only
change the mock's script (spec §0: "רק קלט המטופל ותגובות ה־Mock משתנים").
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from ..case import CaseRecord
from ..naming import AUTOMATIC_ACTIONS, Action

# Execution design decision 3: every automatic action is idempotent; the patient channel
# ignores a repeated idempotency_key.
IDEMPOTENT_ACTIONS = frozenset(a.value for a in AUTOMATIC_ACTIONS)

# action -> (target_system, the patient fields it receives). Sub-project 18 (design §2, D5/D6):
# CheckAppointment may also receive appointment_id - the patient-chosen appointment, stored on
# the case (§11: minimized(appointment_id, appointment_system) is in the spec) - but only when
# the case has one; see present_patient_fields() below.
ACTION_TARGETS: dict[str, tuple[str, tuple[str, ...]]] = {
    Action.CHECK_APPOINTMENT.value: ("appointment_system", ("patient_id", "appointment_id")),
    Action.CHECK_DOCUMENTS.value: ("document_system", ("patient_id",)),
    Action.LOAD_INSTRUCTIONS.value: ("instruction_system", ()),
    Action.SEND_STATUS_UPDATE.value: ("patient_channel", ("patient_id",)),
}


def present_patient_fields(case: CaseRecord, action: str) -> tuple[str, ...]:
    """The patient fields ACTION_TARGETS declares for `action` that this case actually holds.

    Every fixed field (patient_id) is never None, so this changes nothing for them; it exists
    for CheckAppointment's optional appointment_id (sub-project 18, D5/D6), sent - to the real
    system and to the Policy Service's OPA input alike - only when the patient chose one.
    """
    _, fields = ACTION_TARGETS[action]
    return tuple(name for name in fields if getattr(case, name, None) is not None)


# action -> the result fields its owning system may set on DATA_RETRIEVED (design §3.4): each
# system supplies only its own facts, so e.g. the instruction system cannot set held_documents.
# design §5.1 (sub-projects 11-13): the appointment system owns what an appointment requires,
# the document system what the patient holds. Sub-project 18 (D6): the appointment system also
# owns answered_appointment_id, department, exam_type_label, instruction_source_id,
# instruction_version and upcoming_count - each optional in its answer (design §2, an older
# service omits some). Fix round 1 (I2): the *request's* appointment_id is deliberately not
# here - it is write-once from REQUEST_SUBMITTED, never a DATA_RETRIEVED result field, so it
# can never be overwritten by the service's own answered_appointment_id.
RESULT_FIELDS: dict[str, tuple[str, ...]] = {
    Action.CHECK_APPOINTMENT.value: ("appointment_at", "required_documents", "answered_appointment_id",
                                     "department", "exam_type_label", "instruction_source_id",
                                     "instruction_version", "upcoming_count"),
    Action.CHECK_DOCUMENTS.value: ("held_documents",),
    Action.LOAD_INSTRUCTIONS.value: ("instruction_ids",),  # instruction_text goes to the Data Log, not the event
    Action.SEND_STATUS_UPDATE.value: ("delivered",),
}

OK, TRANSIENT_FAILURE, ERROR = "ok", "transient_failure", "error"

# The demo instruction system's text for INSTR-PREP-COLONOSCOPY v3 (LLM design §5: stored in
# the Data Log and re-checked by the Safety Classifier before DATA_RETRIEVED).
INSTRUCTION_TEXT = ("Colonoscopy preparation (INSTR-PREP-COLONOSCOPY v3): clear liquids only on the day "
                    "before the procedure; nothing by mouth after midnight; arrive 30 minutes early.")

# The only third-party error strings audit's policy_reasons may hold (§12.3: IDs/codes only,
# never an arbitrary external message); anything else is reported as "other".
# timeout / rejected: the mocks. The rest: the appointment-service (sub-project 10, design §2.6).
KNOWN_TOOL_ERRORS = frozenset({"timeout", "rejected", "unavailable", "not_found", "patient_not_found",
                               "unauthorized", "invalid_response"})


@dataclass(frozen=True)
class ToolResult:
    kind: str  # ok | transient_failure | error
    data: dict[str, Any] = field(default_factory=dict)


class ToolGateway(Protocol):
    def call(self, action: str, parameters: Mapping[str, Any], idempotency_key: str) -> ToolResult: ...

    def idempotent(self, action: str) -> bool: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


class MockGateway:
    """Deterministic demo systems: a colonoscopy appointment carrying its own required documents
    (design §5.1 - the appointment system owns them), a referral already held, a blood test
    still missing, the approved preparation instructions, and a patient channel."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = _utcnow,
        hours_until_appointment: float = 96,
        required_documents: tuple[str, ...] = ("referral", "blood_test"),
        held_documents: tuple[str, ...] = ("referral",),
        failures: Mapping[str, int] | None = None,
        errors: frozenset[str] = frozenset(),
        non_idempotent: frozenset[str] = frozenset(),
    ) -> None:
        self.clock = clock
        self.hours_until_appointment = hours_until_appointment
        self.required_documents, self.held_documents = required_documents, held_documents
        self.failures = dict(failures or {})
        self.errors, self.non_idempotent = errors, non_idempotent
        self.calls: list[tuple[str, dict[str, Any], str]] = []
        self.delivered: dict[str, dict[str, Any]] = {}  # idempotency_key -> message (deduplicated)

    def idempotent(self, action: str) -> bool:
        return action in IDEMPOTENT_ACTIONS and action not in self.non_idempotent

    def call(self, action: str, parameters: Mapping[str, Any], idempotency_key: str) -> ToolResult:
        self.calls.append((action, dict(parameters), idempotency_key))
        if self.failures.get(action, 0) > 0:
            self.failures[action] -= 1
            return ToolResult(TRANSIENT_FAILURE, {"error": "timeout"})
        if action in self.errors:
            return ToolResult(ERROR, {"error": "rejected"})
        match action:
            case Action.CHECK_APPOINTMENT.value:
                at = self.clock() + timedelta(hours=self.hours_until_appointment)
                # Sub-project 18 (D7): the mock's own instruction source, so a case without a
                # real appointment-service still resolves to the demo colonoscopy instructions
                # and the three golden traces (§0, §15) keep loading them unchanged.
                return ToolResult(OK, {"appointment_at": at, "required_documents": list(self.required_documents),
                                       "instruction_source_id": "INSTR-PREP-COLONOSCOPY", "instruction_version": "3"})
            case Action.CHECK_DOCUMENTS.value:
                return ToolResult(OK, {"held_documents": list(self.held_documents)})
            case Action.LOAD_INSTRUCTIONS.value:
                # Fix round 1 (m1): the mock is itself a (fake) instruction system - it must
                # answer only for the one source it actually knows, exactly like the real
                # appointment-service's instructions endpoint would for an unknown source_id -
                # never silently hand back the demo text for a different one.
                if (parameters.get("source_id"), parameters.get("version")) != ("INSTR-PREP-COLONOSCOPY", "3"):
                    return ToolResult(ERROR, {"error": "not_found"})
                return ToolResult(OK, {"instruction_ids": ["INSTR-PREP-COLONOSCOPY:3"],
                                       "instruction_text": INSTRUCTION_TEXT})
            case Action.SEND_STATUS_UPDATE.value:
                self.delivered.setdefault(idempotency_key, dict(parameters))
                return ToolResult(OK, {"delivered": True})
        return ToolResult(ERROR, {"error": f"unknown action {action}"})
