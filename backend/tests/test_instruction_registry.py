"""Sub-project 18 (design D12): the Approved Source Registry check the instruction routes use.

Fix round 1 (I1): `is_approved` now delegates entirely to the real OPA (`opa_runner`) - there is
one implementation of "is this source approved right now", never a second Python
reimplementation of `policy.rego`'s own time parsing. This file both exercises `is_approved`
against the real, production registry, and proves - for every real entry plus several synthetic
"looks like a date but isn't RFC3339" ones - that it agrees with `opa_runner.instruction_source_approved`
asked directly, i.e. that the two names really are the same code path.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from hospital_agent import instruction_registry
from hospital_agent.instruction_registry import is_approved
from hospital_agent.policy import opa_runner
from hospital_agent.policy.opa_runner import DATA_DIR, instruction_source_approved

_REGISTRY = json.loads((DATA_DIR / "approved_instruction_sources.json").read_text(encoding="utf-8"))
REAL_ENTRIES: dict = _REGISTRY["hospital_agent"]["approved_instruction_sources"]

# Fix round 1 (I1, M1): several date shapes that look plausible but are not RFC3339 - a space
# separator, a "basic" (no separators) form, no seconds, a numeric (non-colon) UTC offset, an
# ISO week date, and (M1) a naive valid_until with no offset at all. `time.parse_rfc3339_ns`
# rejects all six, which makes `instruction_source_approved` undefined - never approved.
ODD_FORMATS: dict = {
    "SPACE_SEPARATOR": {"approved": True, "version": "1",
                       "valid_from": "2024-01-01 00:00:00Z", "valid_until": "2099-01-01T00:00:00Z"},
    "BASIC_FORMAT": {"approved": True, "version": "1",
                    "valid_from": "20240101T000000Z", "valid_until": "2099-01-01T00:00:00Z"},
    "MISSING_SECONDS": {"approved": True, "version": "1",
                       "valid_from": "2024-01-01T00:00Z", "valid_until": "2099-01-01T00:00:00Z"},
    "NUMERIC_OFFSET": {"approved": True, "version": "1",
                      "valid_from": "2024-01-01T00:00:00+0300", "valid_until": "2099-01-01T00:00:00Z"},
    "WEEK_DATE": {"approved": True, "version": "1",
                 "valid_from": "2024-W01-1T00:00:00Z", "valid_until": "2099-01-01T00:00:00Z"},
    "NAIVE_VALID_UNTIL": {"approved": True, "version": "1",
                         "valid_from": "2024-01-01T00:00:00Z", "valid_until": "2099-01-01T00:00:00"},
}


@pytest.fixture
def merged_data_dir(tmp_path):
    """Every real registry entry plus the odd-format synthetic ones above, in a throwaway
    DATA_DIR - never the production file itself."""
    merged = {"hospital_agent": {"approved_instruction_sources": {**REAL_ENTRIES, **ODD_FORMATS}}}
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "approved_instruction_sources.json").write_text(json.dumps(merged), encoding="utf-8")
    return data_dir


# --- is_approved() against the real, production registry -----------------------------------

def test_an_approved_and_current_source_is_approved():
    assert is_approved("INSTR-PREP-COLONOSCOPY", "3") is True


def test_an_unlisted_source_is_not_approved():
    assert is_approved("INSTR-NO-SUCH-THING", "1") is False


def test_the_wrong_version_is_not_approved():
    assert is_approved("INSTR-PREP-COLONOSCOPY", "99") is False


def test_an_expired_source_is_not_approved():
    # INSTR-RETIRED-2025: valid_until 2025-12-31 - long past.
    assert is_approved("INSTR-RETIRED-2025", "1") is False


def test_a_not_yet_valid_source_is_not_approved():
    # INSTR-DRAFT-2090: valid_from 2090-01-01 - long from now.
    assert is_approved("INSTR-DRAFT-2090", "1") is False


def test_an_empty_version_is_not_approved():
    """Rego's own nonempty(version) check (fix round 1 I1: still enforced, now by OPA itself,
    not a Python re-check)."""
    assert is_approved("INSTR-PREP-COLONOSCOPY", "") is False


# --- OPA itself cannot be asked: unavailable (None), never "approved" -----------------------
# Task 8 (carried Task 6 Minor): an outage is reported separately from a real deny, so the route
# can answer 503 instructions_unavailable instead of 404 instruction_not_approved. Both stay
# closed - neither is ever True.

class _Done:
    def __init__(self, stdout, returncode=0):
        self.stdout, self.returncode = stdout, returncode


def test_missing_binary_is_unavailable():
    assert instruction_source_approved("INSTR-PREP-COLONOSCOPY", "3", opa_binary="/nonexistent/opa") is None


def test_timeout_is_unavailable():
    assert instruction_source_approved("INSTR-PREP-COLONOSCOPY", "3", timeout=0.000001) is None


def test_a_non_zero_exit_is_unavailable(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Done("", returncode=1))
    assert instruction_source_approved("INSTR-PREP-COLONOSCOPY", "3") is None


@pytest.mark.parametrize("stdout", [
    "not json",
    "[]",                                    # JSON, but not an object
    '{"result": []}',                        # a result with no expression
    '{"result": [{"expressions": []}]}',
    '{"result": [{"expressions": [{}]}]}',   # an expression with no value
    '{"errors": []}',                        # an object without "result" that is not exactly {}
])
def test_unreadable_output_is_unavailable(monkeypatch, stdout):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Done(stdout))
    assert instruction_source_approved("INSTR-PREP-COLONOSCOPY", "3") is None


def test_is_approved_passes_unavailable_through(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Done("not json"))
    assert is_approved("INSTR-PREP-COLONOSCOPY", "3") is None


# --- OPA answered, and the answer is not "approved": a real deny (False) ---------------------

def test_an_undefined_rule_is_a_deny(monkeypatch):
    """opa eval --format json prints a bare {} (no "result" key at all) when a partial rule
    like instruction_source_approved is undefined for the given input - that is OPA's own
    "not approved" answer (unlisted, wrong version, outside the validity window), so it is a
    deny, never an outage."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Done("{}"))
    assert instruction_source_approved("INSTR-PREP-COLONOSCOPY", "3") is False


@pytest.mark.parametrize("value", ['"yes"', "false", "1", "null"])
def test_a_non_true_answer_is_a_deny(monkeypatch, value):
    stdout = '{"result": [{"expressions": [{"value": %s}]}]}' % value  # not literally true
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Done(stdout))
    assert instruction_source_approved("INSTR-PREP-COLONOSCOPY", "3") is False


# --- I1: every real entry, plus the odd-format ones, agree between is_approved() and OPA ---

@pytest.mark.parametrize("source_id", sorted(REAL_ENTRIES))
def test_every_real_registry_entry_agrees_with_opa(monkeypatch, merged_data_dir, source_id):
    monkeypatch.setattr(opa_runner, "DATA_DIR", merged_data_dir)
    version = REAL_ENTRIES[source_id]["version"]
    # The two currently non-current real entries (see the dedicated tests above); every other
    # real entry is approved: true and within its wide 2026..2030 validity window right now.
    expected = source_id not in ("INSTR-RETIRED-2025", "INSTR-DRAFT-2090")
    assert is_approved(source_id, version) is expected
    assert instruction_source_approved(source_id, version) is expected


@pytest.mark.parametrize("source_id", sorted(ODD_FORMATS))
def test_every_odd_date_format_is_denied(monkeypatch, merged_data_dir, source_id):
    monkeypatch.setattr(opa_runner, "DATA_DIR", merged_data_dir)
    assert is_approved(source_id, "1") is False
    assert instruction_source_approved(source_id, "1") is False


def test_the_real_registry_file_exists_and_parses():
    assert (DATA_DIR / "approved_instruction_sources.json").exists()
    assert "INSTR-NEURO-VISIT" in REAL_ENTRIES


# --- final review M5: the memo in front of OPA --------------------------------------------

class _CountingOpa:
    """Stands in for opa_runner.instruction_source_approved: counts calls, answers `answer`."""

    def __init__(self, answer):
        self.answer, self.calls = answer, 0

    def __call__(self, source_id, version):
        self.calls += 1
        return self.answer


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(instruction_registry, "_clock", lambda: now[0])
    return now


@pytest.mark.parametrize("answer", [True, False])
def test_a_second_read_within_the_ttl_does_not_ask_opa(monkeypatch, clock, answer):
    opa = _CountingOpa(answer)
    monkeypatch.setattr(opa_runner, "instruction_source_approved", opa)
    assert is_approved("INSTR-PREP-COLONOSCOPY", "3") is answer
    clock[0] += instruction_registry.CACHE_TTL_SECONDS - 1
    assert is_approved("INSTR-PREP-COLONOSCOPY", "3") is answer
    assert opa.calls == 1


def test_after_the_ttl_opa_is_asked_again(monkeypatch, clock):
    opa = _CountingOpa(True)
    monkeypatch.setattr(opa_runner, "instruction_source_approved", opa)
    assert is_approved("INSTR-PREP-COLONOSCOPY", "3") is True
    clock[0] += instruction_registry.CACHE_TTL_SECONDS
    opa.answer = False  # e.g. the source expired meanwhile
    assert is_approved("INSTR-PREP-COLONOSCOPY", "3") is False
    assert opa.calls == 2


def test_an_outage_is_never_cached(monkeypatch, clock):
    opa = _CountingOpa(None)
    monkeypatch.setattr(opa_runner, "instruction_source_approved", opa)
    assert is_approved("INSTR-PREP-COLONOSCOPY", "3") is None
    opa.answer = True  # OPA is back
    assert is_approved("INSTR-PREP-COLONOSCOPY", "3") is True
    assert opa.calls == 2


def test_the_memo_is_keyed_by_source_and_version(monkeypatch, clock):
    opa = _CountingOpa(True)
    monkeypatch.setattr(opa_runner, "instruction_source_approved", opa)
    is_approved("INSTR-PREP-COLONOSCOPY", "3")
    is_approved("INSTR-PREP-COLONOSCOPY", "4")
    is_approved("INSTR-NEURO-VISIT", "3")
    assert opa.calls == 3


def test_the_memo_is_bounded(monkeypatch, clock):
    opa = _CountingOpa(True)
    monkeypatch.setattr(opa_runner, "instruction_source_approved", opa)
    for n in range(instruction_registry.CACHE_MAX_ENTRIES + 10):
        is_approved(f"INSTR-{n}", "1")
    assert len(instruction_registry._memo) == instruction_registry.CACHE_MAX_ENTRIES
    calls = opa.calls
    is_approved("INSTR-0", "1")  # the oldest was evicted, so OPA is asked again
    assert opa.calls == calls + 1


def test_the_memo_survives_concurrent_reads(monkeypatch):
    """FastAPI runs the sync instruction routes on a thread pool: concurrent reads and evictions
    must neither raise nor overgrow the memo."""
    import threading

    monkeypatch.setattr(opa_runner, "instruction_source_approved", lambda s, v: True)
    errors = []

    def worker(offset):
        try:
            for n in range(300):
                assert is_approved(f"INSTR-{(n + offset) % 300}", "1") is True
        except Exception as failure:  # noqa: BLE001 - collected and asserted below
            errors.append(failure)

    threads = [threading.Thread(target=worker, args=(i * 37,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(instruction_registry._memo) <= instruction_registry.CACHE_MAX_ENTRIES
