"""Sub-project 18 (design D12): the instruction client behind the UI's instruction routes -
the contract or nothing, the same discipline as test_appointment_list.py."""
import http.client as http_client
import json
import traceback

import pytest

from hospital_agent.execution.http import MAX_BODY_BYTES, HttpResponse
from hospital_agent.instruction_client import (
    InstructionClient,
    InstructionNotFound,
    InstructionUnavailable,
    build_instruction_client,
    map_answer,
)

SOURCE_ID, VERSION = "INSTR-NEURO-VISIT", "1"
GOOD = {"source_id": SOURCE_ID, "version": VERSION, "title": "לפני הביקור", "text": "רשימת תרופות."}


def answer(body, status=200):
    return HttpResponse(status, json.dumps(body).encode())


def test_a_valid_answer_is_parsed():
    result = map_answer(answer(GOOD), source_id=SOURCE_ID, version=VERSION)
    assert (result.source_id, result.version, result.title, result.text) == (
        SOURCE_ID, VERSION, GOOD["title"], GOOD["text"])


def test_instruction_not_found_is_its_own_answer():
    with pytest.raises(InstructionNotFound):
        map_answer(answer({"error": "instruction_not_found"}, status=404), source_id=SOURCE_ID, version=VERSION)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500, 503, 504])
def test_any_other_status_is_unavailable(status):
    with pytest.raises(InstructionUnavailable) as caught:
        map_answer(answer({"error": "x"}, status=status), source_id=SOURCE_ID, version=VERSION)
    assert caught.value.code == ("invalid_response" if status == 404 else f"status_{status}")


@pytest.mark.parametrize("body", [
    [],                                                       # not a dict
    {**GOOD, "source_id": "INSTR-OTHER"},                     # a different source
    {**GOOD, "version": "2"},                                 # a different version
    {k: v for k, v in GOOD.items() if k != "title"},          # missing title
    {**GOOD, "title": ""},
    {**GOOD, "title": "   "},                                 # whitespace only
    {**GOOD, "title": "x" * 201},                             # over the bound
    {**GOOD, "title": 7},                                     # not a string
    {**GOOD, "text": ""},
    {**GOOD, "text": "x" * 4001},
    {**GOOD, "text": None},
])
def test_anything_but_the_contract_is_invalid(body):
    with pytest.raises(InstructionUnavailable) as caught:
        map_answer(answer(body), source_id=SOURCE_ID, version=VERSION)
    assert caught.value.code == "invalid_response"


def test_title_and_text_at_exactly_the_bound_are_ok():
    body = {**GOOD, "title": "x" * 200, "text": "y" * 4000}
    result = map_answer(answer(body), source_id=SOURCE_ID, version=VERSION)
    assert len(result.title) == 200 and len(result.text) == 4000


def test_an_oversized_or_non_json_body_is_invalid():
    for response in (HttpResponse(200, b"x" * MAX_BODY_BYTES), HttpResponse(200, b"not json")):
        with pytest.raises(InstructionUnavailable) as caught:
            map_answer(response, source_id=SOURCE_ID, version=VERSION)
        assert caught.value.code == "invalid_response"


def test_deeply_nested_json_is_invalid():
    nested = b"[" * 20000 + b"]" * 20000
    with pytest.raises(InstructionUnavailable) as caught:
        map_answer(HttpResponse(200, nested), source_id=SOURCE_ID, version=VERSION)
    assert caught.value.code == "invalid_response"


def test_the_client_sends_the_source_version_and_key():
    seen = {}

    def transport(method, url, headers, body, timeout):
        seen.update(method=method, url=url, headers=headers, timeout=timeout)
        return answer(GOOD)

    client = InstructionClient("http://svc:8080/", "k", transport=transport)
    client.get(SOURCE_ID, VERSION)
    assert seen["method"] == "GET"
    assert seen["url"] == f"http://svc:8080/api/v1/instructions/{SOURCE_ID}?version={VERSION}"
    assert seen["headers"]["X-API-Key"] == "k" and seen["timeout"] == 5.0


def test_no_answer_is_unavailable_and_names_no_host():
    def transport(*_):
        raise OSError("connect to svc:8080 refused")

    client = InstructionClient("http://svc:8080", "k", transport=transport)
    with pytest.raises(InstructionUnavailable) as caught:
        client.get(SOURCE_ID, VERSION)
    assert caught.value.code == "no_answer"
    assert caught.value.__suppress_context__ is True
    assert "svc" not in "".join(traceback.format_exception(caught.value))
    assert "svc" not in repr(InstructionClient("http://svc:8080", "secret-k"))


def test_a_transport_http_exception_is_also_no_answer():
    def transport(*_):
        raise http_client.HTTPException("bad")

    with pytest.raises(InstructionUnavailable) as caught:
        InstructionClient("http://svc:8080", "k", transport=transport).get(SOURCE_ID, VERSION)
    assert caught.value.code == "no_answer"


@pytest.mark.parametrize("env, built", [
    ({}, False),
    ({"APPOINTMENT_SERVICE_URL": "http://svc:8080"}, False),
    ({"APPOINTMENT_SERVICE_URL": "ftp://svc", "APPOINTMENT_API_KEY": "k"}, False),
    ({"APPOINTMENT_SERVICE_URL": "http://svc:8080", "APPOINTMENT_API_KEY": "k"}, True),
])
def test_build_instruction_client_needs_both_variables(env, built):
    assert (build_instruction_client(env) is not None) is built
