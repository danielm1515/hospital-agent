"""Approval Validator for ContentApproval (spec §12.4, §12.5).

The Policy Service loads a content_approval_valid/4 fact into Prolog only after this
check passes (§10: "עובדות אישור נטענות רק אחרי בדיקת Approval Validator המלאה").
It mirrors OPA's approval_envelope + human_authorized, so both layers judge the same
record the same way.
"""
from __future__ import annotations

from datetime import datetime

from ..case import ApprovalRecord


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and value.strip() != ""


def content_approval_valid(
    approval: ApprovalRecord | None,
    *,
    case_id: str,
    patient_id: str,
    execution_id: str,
    action: str,
    content_hash: str | None,
    now: datetime,
) -> bool:
    return (
        approval is not None
        and approval.approval_type == "ContentApproval"
        and approval.reviewer_role == "clinical_staff"
        and approval.decision == "approve"
        and all(_nonempty(v) for v in (approval.approval_id, approval.reviewer_id, approval.reason,
                                       approval.shown_context_ref))
        and approval.case_id == case_id
        and approval.patient_id == patient_id
        and approval.execution_id == execution_id
        and approval.action == action
        and _nonempty(approval.content_hash)
        and approval.content_hash == content_hash
        and approval.granted_at <= now < approval.valid_until
        and approval.consumed_at is None
    )
