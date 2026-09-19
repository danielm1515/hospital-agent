"""Readiness feasibility with Z3 (spec §9.1) and the Readiness Check that acts on it.

ask_patient_is_safe() is the §9.1 model unchanged: it searches for a legal SLA
scenario in which an uploaded document would NOT be verified and reviewed before
the appointment. Only UNSAT (no such scenario) makes asking the patient safe;
sat, unknown, a timeout, a Z3 error or an invalid deadline all escalate.

The spec injects an audit service into the Z3 function; here the verdict is
returned and the Readiness Check records it (Policy design decision 2): an
escalation keeps the counterexample in its audit row's policy_reasons.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from math import isfinite

from z3 import And, Ints, Or, Solver, Z3Exception, sat, unsat

from ..naming import Component, EscalationKind, Event, State
from ..state_manager import StateManager, TransitionResult

Z3_TIMEOUT_MS = 5000
# Policy design decision 3: the longest legal upload window in the §9.1 model.
PATIENT_UPLOAD_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class Z3Verdict:
    safe: bool
    result: str  # unsat | sat | unknown | invalid_deadline | error
    detail: str | None = None  # the counterexample model, or the reason Z3 gave


def ask_patient_is_safe(hours_until: object) -> Z3Verdict:
    if type(hours_until) not in (int, float) or not isfinite(hours_until) or hours_until < 0:
        return Z3Verdict(False, "invalid_deadline")
    try:
        upload_h, verify_h, review_h, doc_type = Ints("upload_h verify_h review_h doc_type")
        s = Solver()
        s.set(timeout=Z3_TIMEOUT_MS)
        s.add(review_h >= 0, review_h <= 4)
        s.add(Or(
            And(doc_type == 1, upload_h >= 0, upload_h <= 24, verify_h >= 1, verify_h <= 2),
            And(doc_type == 2, upload_h >= 0, upload_h <= 8, verify_h >= 4, verify_h <= 8)))
        s.add(upload_h + verify_h + review_h > hours_until)
        result = s.check()
        if result == unsat:
            return Z3Verdict(True, "unsat")
        if result == sat:
            return Z3Verdict(False, "sat", str(s.model()))
        return Z3Verdict(False, "unknown", s.reason_unknown())
    except Z3Exception as exc:
        return Z3Verdict(False, "error", str(exc))


class ReadinessCheck:
    """Runs in AssessingReadiness and emits the §3 outcome (Readiness Check, inside the Policy Service)."""

    def __init__(self, state_manager: StateManager, *, z3=ask_patient_is_safe) -> None:
        self.state_manager, self.z3 = state_manager, z3

    def run(self, case_id: str, hours_until: object) -> TransitionResult:
        sm = self.state_manager
        if sm.load(case_id).readiness_complete:
            return sm.apply(case_id, Event.READINESS_PASSED, {}, Component.READINESS_CHECK)
        verdict = self.z3(hours_until)
        if verdict.safe:
            payload = {"z3_result": verdict.result, "patient_deadline": sm.clock() + PATIENT_UPLOAD_WINDOW}
            return sm.apply(case_id, Event.MISSING_INFORMATION_DETECTED, payload, Component.READINESS_CHECK)
        reasons = [f"z3:{verdict.result}"] + ([f"z3_detail:{verdict.detail}"] if verdict.detail else [])
        return sm.escalation.signal(case_id, EscalationKind.Z3_COUNTEREXAMPLE, State.ASSESSING_READINESS,
                                    Component.READINESS_CHECK, reasons=reasons)
