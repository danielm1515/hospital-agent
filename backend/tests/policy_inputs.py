"""OPA inputs shared by the OPA tests: the spec §8 example and its documented variants.

Validity windows are years away from "now", so the real OPA (which reads the clock)
and tests/opa_reference.py agree regardless of when the tests run.
"""
from __future__ import annotations

import copy
from typing import Any

PLAN = [
    {"step": 1, "action": "CheckAppointment"},
    {"step": 2, "action": "CheckDocuments"},
    {"step": 3, "action": "LoadInstructions"},
    {"step": 4, "action": "SendStatusUpdate"},
]
PLAN_HASH = "70471a828372f98f7949666f569537f3bc213e64aa0d3ec84f55ec9e2ba8cb99"

# spec §8 "// input", verbatim values
SPEC_INPUT: dict[str, Any] = {
    "case_id": "CASE-482",
    "patient_id": "P-10041",
    "execution_id": "EXEC-482-02",
    "identity_verified": True,
    "safety_level": "MediumRisk",
    "execution": {"attempt_count": 0, "max_attempts": 3},
    "plan": {"current_step": 2, "plan_hash": PLAN_HASH, "ordered_steps": PLAN},
    "proposed_action": {"action": "CheckDocuments", "from_step": 2, "target_system": "document_system",
                        "parameters": {"patient_fields": ["patient_id", "document_id"]}},
    "approval": None,
}


def approval(**changes: Any) -> dict[str, Any]:
    base = {
        "approval_id": "APPR-1", "approval_type": "ContentApproval", "case_id": "CASE-482",
        "patient_id": "P-10041", "execution_id": "EXEC-482-02", "action": "SendStatusUpdate",
        "content_hash": "HASH-DEMO-001", "reviewer_id": "coordinator_nurse", "reviewer_role": "clinical_staff",
        "decision": "approve", "reason": "checked the message", "shown_context_ref": "ctx-1",
        "granted_at": "2020-01-01T00:00:00Z", "valid_until": "2099-01-01T00:00:00Z", "consumed_at": None,
        "escalation_kind": None, "plan_hash": None, "current_step": None,
    }
    return {**base, **changes}


def variant(**changes: Any) -> dict[str, Any]:
    """SPEC_INPUT with top-level keys replaced; nested dicts are replaced whole."""
    result = copy.deepcopy(SPEC_INPUT)
    result.update(copy.deepcopy(changes))
    return result


def medical_fixture(**changes: Any) -> dict[str, Any]:
    """§8's separate fixture: SendStatusUpdate at step 4 with medical content and no approval."""
    fixture = {
        "plan": {"current_step": 4, "plan_hash": PLAN_HASH, "ordered_steps": PLAN},
        "proposed_action": {"action": "SendStatusUpdate", "from_step": 4, "target_system": "patient_channel",
                            "parameters": {"patient_fields": ["patient_id"]}},
        "outgoing_message": {"evaluated": True, "medical_content_flag": True, "content_hash": "HASH-DEMO-001"},
    }
    return variant(**{**fixture, **changes})


def policy_review_override(**changes: Any) -> dict[str, Any]:
    defaults = {"approval_type": "WorkflowDecision", "escalation_kind": "PolicyReview",
                "escalated_from_state": "Planning", "plan_hash": PLAN_HASH, "current_step": 2,
                "execution_id": None, "action": None, "content_hash": None}
    return approval(**{**defaults, **changes})


# (name, input, expected decision) - the rows of the table under spec §8
SPEC_8_TABLE: list[tuple[str, dict[str, Any], dict[str, Any]]] = [
    ("as documented", variant(), {"result": "Allow", "reasons": []}),
    ("HighRisk without override", variant(safety_level="HighRisk"),
     {"result": "RequireHumanReview", "reasons": ["human_decision_required"]}),
    ("identity not verified", variant(identity_verified=False),
     {"result": "Deny", "reasons": ["identity_not_verified"]}),
    ("empty patient_id", variant(patient_id=""), {"result": "Deny", "reasons": ["missing_patient_context"]}),
    ("from_step 3", variant(proposed_action={**SPEC_INPUT["proposed_action"], "from_step": 3}),
     {"result": "Deny", "reasons": ["action_not_in_plan"]}),
    ("medical output without approval", medical_fixture(),
     {"result": "Deny", "reasons": ["medical_answer_attempt"]}),
    ("medical output with a valid ContentApproval", medical_fixture(approval=approval()),
     {"result": "Allow", "reasons": []}),
    ("plan changed after PLAN_CREATED",
     variant(plan={"current_step": 2, "plan_hash": PLAN_HASH,
                   "ordered_steps": [*PLAN[:3], {"step": 4, "action": "CheckAppointment"}]}),
     {"result": "Deny", "reasons": ["plan_modified"]}),
    ("patient_text sent out",
     variant(proposed_action={**SPEC_INPUT["proposed_action"],
                              "parameters": {"patient_fields": ["patient_id", "patient_text"]}}),
     {"result": "Deny", "reasons": ["field_not_minimized"]}),
    ("attempts exhausted", variant(execution={"attempt_count": 3, "max_attempts": 3}),
     {"result": "Deny", "reasons": ["attempts_exhausted"]}),
    ("HighRisk with a valid one-shot override",
     variant(safety_level="HighRisk", intent="AppointmentPreparation",
             policy_review_override=policy_review_override()),
     {"result": "Allow", "reasons": []}),
]

# More inputs for the OPA-vs-reference agreement test: every deny rule, alone where possible.
EDGE_CASES: list[tuple[str, dict[str, Any]]] = [
    ("unknown safety level", variant(safety_level="Bogus")),
    ("patient verification failed", variant(patient={"verification_status": "failed"})),
    ("unsupported action", variant(proposed_action={**SPEC_INPUT["proposed_action"], "action": "DeleteRecords"})),
    ("negative attempt budget", variant(execution={"attempt_count": -1, "max_attempts": 3})),
    ("message not evaluated", medical_fixture(outgoing_message={"evaluated": False, "medical_content_flag": False,
                                                                "content_hash": "H"})),
    ("medical with a WorkflowDecision", medical_fixture(approval=approval(approval_type="WorkflowDecision"))),
    ("approval for another hash", medical_fixture(approval=approval(content_hash="OTHER"))),
    ("expired approval", medical_fixture(approval=approval(valid_until="2021-01-01T00:00:00Z"))),
    ("consumed approval", medical_fixture(approval=approval(consumed_at="2021-01-01T00:00:00Z"))),
    ("admin_staff ContentApproval", medical_fixture(approval=approval(reviewer_role="admin_staff"))),
    ("empty patient field", variant(proposed_action={**SPEC_INPUT["proposed_action"],
                                                     "parameters": {"patient_fields": ["patient_id", ""]}})),
    ("unknown target", variant(proposed_action={**SPEC_INPUT["proposed_action"], "target_system": "billing"})),
    ("document_id to the appointment system",
     variant(plan={"current_step": 1, "plan_hash": PLAN_HASH, "ordered_steps": PLAN},
             proposed_action={"action": "CheckAppointment", "from_step": 1, "target_system": "appointment_system",
                              "parameters": {"patient_fields": ["patient_id", "document_id"]}})),
    ("LoadInstructions from an approved source",
     variant(plan={"current_step": 3, "plan_hash": PLAN_HASH, "ordered_steps": PLAN},
             proposed_action={"action": "LoadInstructions", "from_step": 3, "target_system": "instruction_system",
                              "parameters": {"patient_fields": []}},
             instruction_source={"source_id": "INSTR-PREP-COLONOSCOPY", "version": "3"})),
    ("LoadInstructions from an unknown source",
     variant(plan={"current_step": 3, "plan_hash": PLAN_HASH, "ordered_steps": PLAN},
             proposed_action={"action": "LoadInstructions", "from_step": 3, "target_system": "instruction_system",
                              "parameters": {"patient_fields": []}},
             instruction_source={"source_id": "INSTR-UNKNOWN", "version": "3"})),
    ("LoadInstructions with the wrong version",
     variant(plan={"current_step": 3, "plan_hash": PLAN_HASH, "ordered_steps": PLAN},
             proposed_action={"action": "LoadInstructions", "from_step": 3, "target_system": "instruction_system",
                              "parameters": {"patient_fields": []}},
             instruction_source={"source_id": "INSTR-PREP-COLONOSCOPY", "version": "2"})),
    ("LoadInstructions from an expired source",
     variant(plan={"current_step": 3, "plan_hash": PLAN_HASH, "ordered_steps": PLAN},
             proposed_action={"action": "LoadInstructions", "from_step": 3, "target_system": "instruction_system",
                              "parameters": {"patient_fields": []}},
             instruction_source={"source_id": "INSTR-RETIRED-2025", "version": "1"})),
    ("LoadInstructions from a source not valid yet",
     variant(plan={"current_step": 3, "plan_hash": PLAN_HASH, "ordered_steps": PLAN},
             proposed_action={"action": "LoadInstructions", "from_step": 3, "target_system": "instruction_system",
                              "parameters": {"patient_fields": []}},
             instruction_source={"source_id": "INSTR-DRAFT-2090", "version": "1"})),
    # Fix round 1, m3: a case with no source at all (D7 - the Orchestrator passes None, never a
    # substitute) must deny unapproved_instruction_source exactly like an unknown one.
    ("LoadInstructions with no source at all",
     variant(plan={"current_step": 3, "plan_hash": PLAN_HASH, "ordered_steps": PLAN},
             proposed_action={"action": "LoadInstructions", "from_step": 3, "target_system": "instruction_system",
                              "parameters": {"patient_fields": []}},
             instruction_source=None)),
    # Fix round 1, m3: an Allow using one of the 13 catalog sources the registry gained in Task 4.
    ("LoadInstructions from a catalog source",
     variant(plan={"current_step": 3, "plan_hash": PLAN_HASH, "ordered_steps": PLAN},
             proposed_action={"action": "LoadInstructions", "from_step": 3, "target_system": "instruction_system",
                              "parameters": {"patient_fields": []}},
             instruction_source={"source_id": "INSTR-CARD-STRESS", "version": "1"})),
    ("override for another step", variant(safety_level="HighRisk", intent="AppointmentPreparation",
                                           policy_review_override=policy_review_override(current_step=3))),
    ("override on a medical question", variant(safety_level="HighRisk", intent="MedicalQuestion",
                                               policy_review_override=policy_review_override())),
    ("CriticalRisk with override, patient-facing",
     medical_fixture(safety_level="CriticalRisk", intent="AppointmentPreparation",
                     outgoing_message={"evaluated": True, "medical_content_flag": False, "content_hash": "H"},
                     policy_review_override=policy_review_override(current_step=4))),
    ("no plan at all", variant(plan={"current_step": None, "plan_hash": None, "ordered_steps": None})),
    ("everything missing", {}),
]
