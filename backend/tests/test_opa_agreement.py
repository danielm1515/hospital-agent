"""The real OPA and tests/opa_reference.py must return the same decision on every input (Policy design §3)."""
import json
import time

import pytest

from hospital_agent.policy import opa_runner
from tests import opa_reference
from tests.policy_inputs import EDGE_CASES, SPEC_8_TABLE

CORPUS = [(name, policy_input) for name, policy_input, _ in SPEC_8_TABLE] + EDGE_CASES


def _data() -> dict:
    merged: dict = {"hospital_agent": {}}
    for path in sorted(opa_runner.DATA_DIR.glob("*.json")):
        merged["hospital_agent"].update(json.loads(path.read_text(encoding="utf-8"))["hospital_agent"])
    return merged


@pytest.mark.parametrize("name, policy_input", CORPUS, ids=[name for name, _ in CORPUS])
def test_opa_and_reference_agree(name, policy_input):
    real = opa_runner.evaluate(policy_input)
    reference = opa_reference.evaluate(policy_input, _data(), time.time_ns())
    assert (real.result, list(real.reasons)) == (reference["result"], reference["reasons"])


def test_corpus_covers_every_deny_rule():
    reasons = set()
    for _, policy_input in CORPUS:
        reasons |= set(opa_reference.evaluate(policy_input, _data(), time.time_ns())["reasons"])
    rego = opa_runner.POLICY_FILE.read_text(encoding="utf-8")
    deny_rules = {line.split('"')[1] for line in rego.splitlines() if line.startswith("deny contains")}
    assert deny_rules <= reasons, deny_rules - reasons
