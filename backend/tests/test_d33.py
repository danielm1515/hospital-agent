"""D33 (spec §16): the labelled set of outgoing messages and the Response Evaluator's recall.

§6.5 puts the Response Evaluator on the boundary of INV-2: a false negative is medical
content leaving without an approval, and the quality of the detection is *measured, never
proven*. These tests check the measurement itself - the set, the arithmetic and the report -
and never touch the network: the end-to-end run uses FakeProvider through the real
ResponseEvaluator (its separate process included).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from eval.d33 import (
    D33_RECALL_THRESHOLD,
    FAKE_PROVIDER_MEDICAL_TOTAL,
    FAKE_PROVIDER_TRUE_POSITIVES,
    Message,
    Outcome,
    Report,
    evaluate,
    load_messages,
    main,
    render,
    score,
)
from hospital_agent.execution.gateway import INSTRUCTION_TEXT
from hospital_agent.llm.evaluator import ResponseEvaluator
from hospital_agent.llm.message import TEMPLATE
from hospital_agent.llm.provider import FakeProvider, LLMFailed

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)

# The crude keyword rule of FakeProvider (provider.MEDICAL_WORDS) is English-only, while the
# set is mostly Hebrew, so the fake's recall is far below the declared threshold. That is the
# honest measurement, not a defect of the set: the threshold applies to the live model
# (`python -m eval.d33 --live`). Here the fake is pinned to its own measured value (imported
# from eval.d33, which also quotes it in the report) so the offline suite stays green and any
# drift in the set or the rule shows up as a failure in one place.
FAKE_PROVIDER_RECALL = FAKE_PROVIDER_TRUE_POSITIVES / FAKE_PROVIDER_MEDICAL_TOTAL
FAKE_PROVIDER_FLAGS_MEDICAL = ("MSG-27", "MSG-31", "MSG-37", "MSG-44")  # the only medical texts its rule catches
FAKE_PROVIDER_OVER_FLAGS = ("MSG-16",)  # "medications" in an operational English sentence


def message(id: str, medical: bool, text: str = "text") -> Message:
    return Message(id=id, text=text, medical=medical, kind="kind", note="note")


# --- the labelled set ------------------------------------------------------------------------


def test_the_set_loads_and_is_balanced():
    messages = load_messages()
    assert len(messages) == 48
    assert len({m.id for m in messages}) == 48
    medical = [m for m in messages if m.medical]
    operational = [m for m in messages if not m.medical]
    assert len(medical) >= 20 and len(operational) >= 20
    assert len(medical) + len(operational) == 48
    for m in messages:
        assert m.text.strip(), m.id
        assert m.kind.strip(), m.id
        assert m.note.strip(), m.id


def test_the_set_covers_the_kinds_d33_names():
    messages = load_messages()
    kinds = {m.kind for m in messages}
    assert {"status_template", "appointment", "documents", "instructions_quote", "hours"} <= kinds
    assert {"medication_advice", "result_interpretation", "diagnosis", "treatment_advice",
            "dose_change", "clinical_answer"} <= kinds
    hidden = [m for m in messages if m.kind == "hidden_advice"]
    assert len(hidden) >= 3
    assert all(m.medical for m in hidden)
    assert any(m.text == INSTRUCTION_TEXT and not m.medical for m in messages), \
        "the approved preparation instructions must appear verbatim, labelled operational"
    status = TEMPLATE.format(date="22/09/2026", time="09:30", documents="referral, blood_test",
                             source_id="INSTR-PREP-COLONOSCOPY", version=3)
    assert any(m.text == status and not m.medical for m in messages), \
        "the system's own status message (llm/message.py) must be in the set, unchanged"


def test_every_line_of_the_file_is_one_labelled_object():
    from eval.d33 import DATASET

    for line in DATASET.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        assert set(row) == {"id", "text", "medical", "kind", "note"}
        assert isinstance(row["medical"], bool)


# --- score() ---------------------------------------------------------------------------------


def test_score_counts_a_known_mix():
    outcomes = [
        Outcome(message("M1", True), True),    # TP
        Outcome(message("M2", True), True),    # TP
        Outcome(message("M3", True), True),    # TP
        Outcome(message("M4", True), False),   # FN
        Outcome(message("O1", False), False),  # TN
        Outcome(message("O2", False), False),  # TN
        Outcome(message("O3", False), False),  # TN
        Outcome(message("O4", False), True),   # FP
    ]
    report = score(outcomes, model="fake", prompts_version="v", now=NOW)
    assert (report.total, report.medical, report.operational) == (8, 4, 4)
    assert (report.true_positives, report.false_negatives, report.false_positives) == (3, 1, 1)
    assert report.unusable == 0
    assert report.recall == pytest.approx(0.75)
    assert report.false_negative_rate == pytest.approx(0.25)
    assert report.false_positive_rate == pytest.approx(0.25)
    assert report.precision == pytest.approx(0.75)
    assert report.accuracy == pytest.approx(0.75)
    assert [o.message.id for o in report.missed] == ["M4"]
    assert [o.message.id for o in report.over_flagged] == ["O4"]
    assert report.meets_threshold is False


def test_an_unusable_medical_answer_counts_as_a_false_negative():
    report = score([Outcome(message("M1", True), True), Outcome(message("M2", True), None)],
                   model="fake", prompts_version="v", now=NOW)
    assert report.unusable == 1
    assert report.false_negatives == 1
    assert report.true_positives == 1
    assert [o.message.id for o in report.missed] == ["M2"]
    assert report.recall == pytest.approx(0.5)


def test_an_unusable_operational_answer_is_neither_a_false_positive_nor_a_true_positive():
    report = score([Outcome(message("O1", False), None)], model="fake", prompts_version="v", now=NOW)
    assert (report.unusable, report.false_positives, report.true_positives) == (1, 0, 0)
    assert report.over_flagged == []
    assert report.false_positive_rate == pytest.approx(0.0)
    assert report.accuracy == pytest.approx(0.0)  # an unanswered message is not a correct answer


def test_score_survives_an_empty_or_one_sided_set():
    empty = score([], model="fake", prompts_version="v", now=NOW)
    assert empty.recall == 1.0 and empty.precision == 1.0 and empty.accuracy == 1.0
    assert empty.meets_threshold is True
    operational_only = score([Outcome(message("O1", False), False)],
                             model="fake", prompts_version="v", now=NOW)
    assert operational_only.recall == 1.0
    assert operational_only.false_negative_rate == 0.0


def test_the_threshold_is_the_declared_one():
    assert D33_RECALL_THRESHOLD == 0.95
    just_below = score([Outcome(message(f"M{i}", True), i > 0) for i in range(20)],
                       model="fake", prompts_version="v", now=NOW)
    assert just_below.recall == pytest.approx(0.95)
    assert just_below.meets_threshold is True


# --- evaluate() ------------------------------------------------------------------------------


class StubEvaluator:
    """Answers from a mapping of text -> bool | Exception; records the order of the calls."""

    def __init__(self, answers: dict[str, bool | Exception]) -> None:
        self.answers, self.seen = answers, []

    def evaluate(self, message: str) -> bool:
        self.seen.append(message)
        answer = self.answers[message]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def close(self) -> None:
        pass


def test_evaluate_returns_one_outcome_per_message_in_order():
    messages = [message("M1", True, "a"), message("O1", False, "b"), message("M2", True, "c")]
    stub = StubEvaluator({"a": True, "b": False, "c": True})
    outcomes = evaluate(messages, stub)
    assert [o.message.id for o in outcomes] == ["M1", "O1", "M2"]
    assert [o.flagged for o in outcomes] == [True, False, True]
    assert stub.seen == ["a", "b", "c"]


def test_evaluate_turns_an_llm_failure_into_an_unusable_outcome():
    messages = [message("M1", True, "a"), message("M2", True, "b")]
    outcomes = evaluate(messages, StubEvaluator({"a": LLMFailed("evaluator"), "b": True}))
    assert [o.flagged for o in outcomes] == [None, True]


# --- render() --------------------------------------------------------------------------------


def _demo_report() -> Report:
    outcomes = [
        Outcome(message("M1", True, "take 10 mg"), True),
        Outcome(message("M2", True, "stop the pill"), False),
        Outcome(message("O1", False, "your appointment is on Monday"), False),
        Outcome(message("O2", False, "bring your ID"), True),
    ]
    return score(outcomes, model="fake", prompts_version="abc123def456", now=NOW)


def test_render_is_deterministic_and_states_the_threshold():
    report = _demo_report()
    first = render(report)
    assert render(report) == first
    assert "0.95" in first
    assert "fake" in first and "abc123def456" in first
    assert "2026-09-20" in first
    assert "M2" in first and "stop the pill" in first     # the false negative
    assert "O2" in first and "bring your ID" in first     # the false positive


def test_render_explains_the_number_and_quotes_the_fake_providers_recall():
    report = _demo_report()
    text = render(report)
    assert "measures" in text.lower() and "proves nothing" in text
    assert "not a proof of INV-2" in text
    assert f"{FAKE_PROVIDER_RECALL:.4f}" in text
    assert f"{FAKE_PROVIDER_TRUE_POSITIVES}/{FAKE_PROVIDER_MEDICAL_TOTAL}" in text
    assert "not the product's number" in text


def test_render_escapes_a_pipe_so_the_tables_stay_tables():
    report = score([Outcome(message("M1", True, "a | b"), False)],
                   model="fake", prompts_version="v", now=NOW)
    row = next(line for line in render(report).splitlines() if "M1" in line)
    assert r"\|" in row
    assert row.count("|") - row.count(r"\|") == 6  # 5 cells, not 6: the pipe inside the text is escaped


# --- end to end, offline ---------------------------------------------------------------------


def test_the_fake_provider_run_measures_the_whole_set():
    messages = load_messages()
    evaluator = ResponseEvaluator(FakeProvider())
    try:
        outcomes = evaluate(messages, evaluator)
    finally:
        evaluator.close()
    report = score(outcomes, model="fake", prompts_version="abc123def456", now=NOW)

    assert report.total == 48 and report.unusable == 0
    assert tuple(o.message.id for o in report.missed) == tuple(
        m.id for m in messages if m.medical and m.id not in FAKE_PROVIDER_FLAGS_MEDICAL)
    assert tuple(o.message.id for o in report.over_flagged) == FAKE_PROVIDER_OVER_FLAGS
    assert report.recall == pytest.approx(FAKE_PROVIDER_RECALL)
    assert report.meets_threshold is (FAKE_PROVIDER_RECALL >= D33_RECALL_THRESHOLD)

    text = render(report)
    assert f"{D33_RECALL_THRESHOLD}" in text
    assert "fake" in text
    assert "| Messages | 48 (24 medical, 24 operational) |" in text
    for missed in report.missed:
        assert missed.message.id in text
    assert str(report.false_negatives) in text


def test_main_writes_the_report_and_returns_the_threshold_verdict(tmp_path):
    out = tmp_path / "d33-report.md"
    code = main(["--out", str(out)])
    body = out.read_text(encoding="utf-8")
    assert "# D33" in body and "0.95" in body
    assert code == (0 if FAKE_PROVIDER_RECALL >= D33_RECALL_THRESHOLD else 1)


def test_main_live_without_a_key_says_so(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert main(["--live", "--out", str(tmp_path / "unused.md")]) == 2
    assert "OPENAI_API_KEY" in capsys.readouterr().err
    assert not (tmp_path / "unused.md").exists()
