"""The conformance tests read docs/spec/ - make sure the reader itself works."""
import pytest

from tests.spec_tables import golden_traces, read_table


def test_reads_the_transition_table():
    rows = read_table("03-transitions-guards.md", ("מצב נוכחי", "Event", "Guard", "מצב הבא"))
    assert len(rows) == 41
    assert rows[0] == ["Initial", "REQUEST_SUBMITTED", "PatientIdentified", "Received"]


def test_reads_the_three_golden_traces():
    traces = golden_traces()
    assert sorted(traces) == [1, 2, 3]
    assert traces[2][0] == ("Received", "REQUEST_SUBMITTED")
    assert traces[2][-1] == ("Completed", "HUMAN_RESOLVED_CASE")


def test_unknown_table_header_raises():
    with pytest.raises(LookupError):
        read_table("03-transitions-guards.md", ("no", "such", "header"))
