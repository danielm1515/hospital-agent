"""The spec's Rego, evaluated by the real OPA binary (spec §8, §14)."""
import subprocess

import pytest

from hospital_agent.policy import opa_runner
from hospital_agent.policy.opa_runner import OpaDecision, evaluate
from tests.policy_inputs import SPEC_8_TABLE, SPEC_INPUT
from tests.spec_programs import code_blocks


def test_policy_rego_is_the_spec_8_rego():
    spec_rego = next(body for lang, body in code_blocks("08-opa-policy.md") if lang == "rego")
    assert opa_runner.POLICY_FILE.read_text(encoding="utf-8") == spec_rego + "\n"


def test_spec_8_example_input_gives_the_documented_output():
    assert evaluate(SPEC_INPUT) == OpaDecision("Allow", ())


@pytest.mark.parametrize("name, policy_input, expected", SPEC_8_TABLE, ids=[row[0] for row in SPEC_8_TABLE])
def test_spec_8_table(name, policy_input, expected):
    assert evaluate(policy_input) == OpaDecision(expected["result"], tuple(expected["reasons"]))


def test_missing_binary_fails_closed():
    assert evaluate(SPEC_INPUT, opa_binary="/nonexistent/opa") == OpaDecision("Deny", ("policy_engine_unavailable",))


def test_timeout_fails_closed():
    assert evaluate(SPEC_INPUT, timeout=0.000001) == OpaDecision("Deny", ("policy_engine_unavailable",))


def test_unreadable_output_fails_closed(monkeypatch):
    class Done:
        returncode, stdout = 0, "not json"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Done())
    assert evaluate(SPEC_INPUT) == OpaDecision("Deny", ("policy_engine_unavailable",))


def test_unknown_result_is_no_matching_rule(monkeypatch):
    class Done:
        returncode = 0
        stdout = '{"result": [{"expressions": [{"value": {"result": "Maybe", "reasons": []}}]}]}'

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Done())
    assert evaluate(SPEC_INPUT) == OpaDecision("Deny", ("no_matching_rule",))
