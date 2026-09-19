"""Retry Manager - what follows a failed call (spec §3, §12.1, §14; Execution design §4).

attempt_count has already been incremented when the call started, so after the 3rd
failed attempt of a step and cycle it equals MAX_ATTEMPTS:

    error, or a non-idempotent action     -> escalate NonIdempotentFailure (no automatic retry)
    attempt_count >= MAX_ATTEMPTS         -> RETRY_EXHAUSTED (a human decides)
    otherwise                             -> TOOL_TRANSIENT_FAILURE (back to Planning, re-proposed)
"""
from __future__ import annotations

from dataclasses import dataclass

from ..case import MAX_ATTEMPTS, CaseRecord
from ..naming import EscalationKind, Event


@dataclass(frozen=True)
class RetryVerdict:
    event: Event | None = None
    escalation: EscalationKind | None = None


def after_failure(case: CaseRecord, *, idempotent: bool, transient: bool) -> RetryVerdict:
    if not transient or not idempotent:
        return RetryVerdict(escalation=EscalationKind.NON_IDEMPOTENT_FAILURE)
    if case.attempt_count >= MAX_ATTEMPTS:
        return RetryVerdict(event=Event.RETRY_EXHAUSTED)
    return RetryVerdict(event=Event.TOOL_TRANSIENT_FAILURE)
