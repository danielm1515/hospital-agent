"""Staff-fixes design Task 4: one definition of the case-list groups the Case Monitor
filters by (`?group=` on `GET /api/staff/cases`), so the backend and the frontend
(`frontend/src/pages/staff/labels.ts`) share the same partition instead of drifting apart.

Every State - the spec's 12 plus sub-project 15's extension `AwaitingPatientReply` - belongs
to exactly one group; `tests/test_state_groups.py` checks the partition, the way
`EXTENSION_STATES` is checked against `State` elsewhere. `AwaitingPatientReply` sits in
`patient` beside `AwaitingPatientInput`: without it, a case waiting for the patient's reply
(sub-project 15) would belong to no group.
"""
from __future__ import annotations

from .naming import State

STATE_GROUPS: dict[str, frozenset[State]] = {
    "staff": frozenset({State.AWAITING_HUMAN_REVIEW}),
    "patient": frozenset({State.AWAITING_PATIENT_INPUT, State.AWAITING_PATIENT_REPLY}),
    "automatic": frozenset({
        State.RECEIVED,
        State.CLASSIFYING,
        State.CLASSIFIED,
        State.PLANNING,
        State.RETRIEVING_DATA,
        State.ASSESSING_READINESS,
        State.READY,
        State.DELIVERING,
    }),
    # Labelled "הסתיימו" in the UI, not "הושלמה בהצלחה": Completed also covers a case a
    # reviewer closed without delivery (HUMAN_RESOLVED_CASE, fsm.py).
    "done": frozenset({State.COMPLETED}),
    # Reachable only through HUMAN_REJECTED (fsm.py).
    "rejected": frozenset({State.FAILED}),
}
