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

from ..naming import AUTOMATIC_ACTIONS, Action

# Execution design decision 3: every automatic action is idempotent; the patient channel
# ignores a repeated idempotency_key.
IDEMPOTENT_ACTIONS = frozenset(a.value for a in AUTOMATIC_ACTIONS)

# action -> (target_system, the patient fields it receives)
ACTION_TARGETS: dict[str, tuple[str, tuple[str, ...]]] = {
    Action.CHECK_APPOINTMENT.value: ("appointment_system", ("patient_id",)),
    Action.CHECK_DOCUMENTS.value: ("document_system", ("patient_id",)),
    Action.LOAD_INSTRUCTIONS.value: ("instruction_system", ()),
    Action.SEND_STATUS_UPDATE.value: ("patient_channel", ("patient_id",)),
}

# action -> the result fields its owning system may set on DATA_RETRIEVED (design §3.4): each
# system supplies only its own facts, so e.g. the instruction system cannot set held_documents.
RESULT_FIELDS: dict[str, tuple[str, ...]] = {
    Action.CHECK_APPOINTMENT.value: ("appointment_at",),
    Action.CHECK_DOCUMENTS.value: ("required_documents", "held_documents"),
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
KNOWN_TOOL_ERRORS = frozenset({"timeout", "rejected"})


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
    """Deterministic demo systems: a colonoscopy appointment, a referral already held, a
    blood test still missing, the approved preparation instructions, and a patient channel."""

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
                return ToolResult(OK, {"appointment_at": at})
            case Action.CHECK_DOCUMENTS.value:
                return ToolResult(OK, {"required_documents": list(self.required_documents),
                                       "held_documents": list(self.held_documents)})
            case Action.LOAD_INSTRUCTIONS.value:
                return ToolResult(OK, {"instruction_ids": ["INSTR-PREP-COLONOSCOPY:3"],
                                       "instruction_text": INSTRUCTION_TEXT})
            case Action.SEND_STATUS_UPDATE.value:
                self.delivered.setdefault(idempotency_key, dict(parameters))
                return ToolResult(OK, {"delivered": True})
        return ToolResult(ERROR, {"error": f"unknown action {action}"})
