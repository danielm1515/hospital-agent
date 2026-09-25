"""Sub-project 15's extension of the spec (docs/spec_corrections.md row 83): pinned here, so the
spec's own lists (test_naming, test_fsm) can keep comparing everything outside it exactly."""
from hospital_agent.naming import (
    EVENT_OWNER,
    EXTENSION_EVENTS,
    EXTENSION_STATES,
    EXTERNAL_EVENTS,
    TERMINAL_STATES,
    Event,
    State,
)


def test_the_extension_is_one_state_and_two_external_events():
    assert EXTENSION_STATES == {State.AWAITING_PATIENT_REPLY}
    assert EXTENSION_EVENTS == {Event.PATIENT_REPLY_REQUESTED, Event.PATIENT_REPLY_SUBMITTED}
    assert EXTENSION_EVENTS <= EXTERNAL_EVENTS
    assert not EXTENSION_EVENTS & set(EVENT_OWNER)
    assert not EXTENSION_STATES & TERMINAL_STATES
