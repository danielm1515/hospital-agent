"""Staff-fixes design Task 4: STATE_GROUPS partitions every State exactly once."""
from hospital_agent.naming import State
from hospital_agent.state_groups import STATE_GROUPS


def test_state_groups_partition_every_state_including_the_extension():
    covered: set[State] = set()
    for name, states in STATE_GROUPS.items():
        assert not (covered & states), f"a state may belong to only one group ({name} overlaps)"
        covered |= states
    assert covered == set(State)


def test_the_five_groups_match_the_design():
    assert set(STATE_GROUPS) == {"staff", "patient", "automatic", "done", "rejected"}
    assert STATE_GROUPS["staff"] == {State.AWAITING_HUMAN_REVIEW}
    assert STATE_GROUPS["patient"] == {State.AWAITING_PATIENT_INPUT, State.AWAITING_PATIENT_REPLY}
    assert STATE_GROUPS["done"] == {State.COMPLETED}
    assert STATE_GROUPS["rejected"] == {State.FAILED}
