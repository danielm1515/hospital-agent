"""A second, independent implementation of policy.rego in Python - used only in tests.

tests/test_opa_agreement.py runs this and the real OPA binary on the same inputs and
requires identical decisions, so a misreading of the Rego in either direction shows
up as a disagreement. Rule names and order follow policy.rego (spec §8). Rego's
"undefined" is modelled as the sentinel _U: any comparison with it is false.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from typing import Any

_U = object()  # undefined

RETRIEVAL_ACTIONS = {"CheckAppointment", "CheckDocuments", "LoadInstructions"}
PATIENT_FACING_ACTIONS = {"SendStatusUpdate"}
KNOWN_ACTIONS = RETRIEVAL_ACTIONS | PATIENT_FACING_ACTIONS
LOW_RISK = {"LowRisk", "MediumRisk"}
VALID_SAFETY_LEVELS = LOW_RISK | {"HighRisk", "CriticalRisk"}


def _get(value: Any, *path: Any) -> Any:
    for key in path:
        if isinstance(value, dict) and isinstance(key, str) and key in value:
            value = value[key]
        elif isinstance(value, list) and isinstance(key, int) and 0 <= key < len(value):
            value = value[key]
        else:
            return _U
    return value


def _eq(a: Any, b: Any) -> bool:
    if a is _U or b is _U:
        return False
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    return a == b


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _nonempty(v: Any) -> bool:
    return isinstance(v, str) and v.strip() != ""


def _ns(value: Any) -> int | None:
    """time.parse_rfc3339_ns; None where Rego would be undefined."""
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return int(dt.timestamp()) * 1_000_000_000 + dt.microsecond * 1000


def _lt(a: int | None, b: int | None) -> bool:
    return a is not None and b is not None and a < b


def _le(a: int | None, b: int | None) -> bool:
    return a is not None and b is not None and a <= b


def _marshal(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def evaluate(inp: dict[str, Any], data: dict[str, Any], now_ns: int) -> dict[str, Any]:
    data = data.get("hospital_agent", {})
    action = _get(inp, "proposed_action", "action")

    patient_context_present = all(_nonempty(_get(inp, k)) for k in ("patient_id", "case_id", "execution_id"))
    identity_verified = _eq(_get(inp, "identity_verified"), True)
    safety = _get(inp, "safety_level")
    safety_valid = isinstance(safety, str) and safety in VALID_SAFETY_LEVELS
    known_action = isinstance(action, str) and action in KNOWN_ACTIONS

    step_no = _get(inp, "plan", "current_step")
    action_in_plan = False
    if _is_number(step_no) and step_no == math.floor(step_no) and step_no >= 1:
        step = _get(inp, "plan", "ordered_steps", int(step_no) - 1)
        action_in_plan = (_eq(_get(step, "action"), action)
                          and _eq(_get(inp, "proposed_action", "from_step"), step_no))

    steps = _get(inp, "plan", "ordered_steps")
    plan_intact = (isinstance(steps, list)
                   and _eq(hashlib.sha256(_marshal(steps).encode()).hexdigest(), _get(inp, "plan", "plan_hash")))

    n, m = _get(inp, "execution", "attempt_count"), _get(inp, "execution", "max_attempts")
    attempts_valid = (_is_number(n) and _is_number(m) and n == math.floor(n) and m == math.floor(m)
                      and n >= 0 and m > 0)

    def envelope(a: Any) -> bool:
        return (isinstance(a, dict)
                and all(_nonempty(a.get(k)) for k in ("approval_id", "reviewer_id", "reason", "shown_context_ref"))
                and _eq(_get(a, "case_id"), _get(inp, "case_id"))
                and _eq(_get(a, "patient_id"), _get(inp, "patient_id"))
                and _eq(_get(a, "decision"), "approve")
                and "consumed_at" in a and a["consumed_at"] is None
                and _le(_ns(a.get("granted_at")), now_ns)
                and _lt(now_ns, _ns(a.get("valid_until"))))

    intent = _get(inp, "intent")
    override = _get(inp, "policy_review_override")
    override_valid = (known_action and _nonempty(intent) and intent != "MedicalQuestion"
                      and envelope(override)
                      and _eq(_get(override, "approval_type"), "WorkflowDecision")
                      and _get(override, "reviewer_role") in ("clinical_staff", "admin_staff")
                      and _eq(_get(override, "escalation_kind"), "PolicyReview")
                      and _eq(_get(override, "escalated_from_state"), "Planning")
                      and _eq(_get(override, "plan_hash"), _get(inp, "plan", "plan_hash"))
                      and _eq(_get(override, "current_step"), _get(inp, "plan", "current_step")))

    high = isinstance(safety, str) and safety in ("HighRisk", "CriticalRisk")
    automation_safety_ok = (isinstance(safety, str) and safety in LOW_RISK) or (high and override_valid)
    require_human_review = high and not override_valid

    message = _get(inp, "outgoing_message")
    message_evaluated = (_eq(_get(message, "evaluated"), True)
                         and isinstance(_get(message, "medical_content_flag"), bool)
                         and _nonempty(_get(message, "content_hash")))
    approval = _get(inp, "approval")
    human_authorized = (envelope(approval)
                        and _eq(_get(approval, "approval_type"), "ContentApproval")
                        and _eq(_get(approval, "reviewer_role"), "clinical_staff")
                        and _eq(_get(approval, "execution_id"), _get(inp, "execution_id"))
                        and _eq(_get(approval, "action"), action)
                        and _nonempty(_get(approval, "content_hash"))
                        and _eq(_get(approval, "content_hash"), _get(message, "content_hash")))

    target = _get(inp, "proposed_action", "target_system")
    minimized = data.get("minimized_fields", {})
    target_fields = minimized.get(target) if isinstance(target, str) else _U
    target_known = isinstance(target_fields, list)
    allowed_fields = set(target_fields) if target_known else set()
    fields = _get(inp, "proposed_action", "parameters", "patient_fields")
    patient_fields_valid = isinstance(fields, list) and all(_nonempty(f) for f in fields)

    source_id = _get(inp, "instruction_source", "source_id")
    registry = data.get("approved_instruction_sources", {})
    record = _U if source_id is _U else registry.get(source_id) if isinstance(source_id, str) else None
    version = _get(inp, "instruction_source", "version")
    source_approved = (isinstance(record, dict) and _eq(record.get("approved"), True)
                       and _nonempty(version) and _eq(record.get("version"), version)
                       and _le(_ns(record.get("valid_from")), now_ns)
                       and _lt(now_ns, _ns(record.get("valid_until"))))
    medical_flag = _eq(_get(message, "medical_content_flag"), True)

    deny: set[str] = set()
    if not safety_valid:
        deny.add("invalid_safety_level")
    if not identity_verified:
        deny.add("identity_not_verified")
    if _eq(_get(inp, "patient", "verification_status"), "failed"):
        deny.add("patient_verification_failed")
    if not patient_context_present:
        deny.add("missing_patient_context")
    if not action_in_plan:
        deny.add("action_not_in_plan")
    if not known_action:
        deny.add("action_not_supported")
    if not plan_intact:
        deny.add("plan_modified")
    if not attempts_valid:
        deny.add("invalid_attempt_budget")
    if attempts_valid and n >= m:
        deny.add("attempts_exhausted")
    if action in PATIENT_FACING_ACTIONS and not message_evaluated:
        deny.add("message_not_evaluated")
    if medical_flag and not human_authorized:
        deny.add("medical_answer_attempt")
    if medical_flag and _eq(_get(approval, "approval_type"), "WorkflowDecision"):
        deny.add("approval_is_workflow_only")
    if not patient_fields_valid:
        deny.add("invalid_patient_fields")
    if isinstance(fields, list) and any(f not in allowed_fields for f in fields):
        deny.add("field_not_minimized")
    if known_action and not target_known:
        deny.add("unknown_target_system")
    if action == "LoadInstructions":
        if not source_approved:
            deny.add("unapproved_instruction_source")
        if isinstance(record, dict):
            if _le(_ns(record.get("valid_until")), now_ns):
                deny.add("instruction_source_expired")
            if _lt(now_ns, _ns(record.get("valid_from"))):
                deny.add("instruction_source_not_yet_valid")

    allow = (identity_verified and patient_context_present and automation_safety_ok and action_in_plan
             and not deny and (action in RETRIEVAL_ACTIONS or (action in PATIENT_FACING_ACTIONS and message_evaluated)))

    if deny:
        return {"result": "Deny", "reasons": sorted(deny)}
    if require_human_review:
        return {"result": "RequireHumanReview", "reasons": ["human_decision_required"]}
    if allow:
        return {"result": "Allow", "reasons": []}
    return {"result": "Deny", "reasons": ["no_matching_rule"]}
