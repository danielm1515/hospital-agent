"""CheckDocuments against the owner's document-service (sub-project 13, design §5.2).

Only CheckDocuments leaves over HTTP; the other three actions are the fallback's (design §5.1:
the appointment system supplies `required_documents`, the document system what the patient
holds - `held_documents` here). Built the same way as `appointment_service.py`, sharing the
transport in `.http`.
"""
from __future__ import annotations

import http.client as http_client
import json
import os
import urllib.parse
from collections.abc import Callable, Mapping
from typing import Any

from ..naming import Action
from . import http
from .gateway import ERROR, OK, TRANSIENT_FAILURE, MockGateway, ToolGateway, ToolResult
from .http import HttpResponse, no_answer_result

TIMEOUT_SECONDS = 10.0
_TRANSIENT = {500: "unavailable", 502: "unavailable", 503: "unavailable", 504: "timeout"}

# (method, url, headers, body, timeout) -> the response; raises OSError when there is no answer.
Transport = Callable[[str, str, Mapping[str, str], bytes | None, float], HttpResponse]


def _urllib_transport(method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float) -> HttpResponse:
    return http.request(method, url, headers, body, timeout)


def _error(code: str) -> ToolResult:
    return ToolResult(ERROR, {"error": code})


def _json(body: bytes) -> Any:
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None


def map_documents(response: HttpResponse) -> ToolResult:
    """design §5.2: the held document types are the accepted documents', sorted and deduplicated."""
    if response.status in _TRANSIENT:
        return ToolResult(TRANSIENT_FAILURE, {"error": _TRANSIENT[response.status]})
    if response.status in (401, 403):
        return _error("unauthorized")
    if response.status != 200:
        return _error("invalid_response")
    body = _json(response.body)
    if not isinstance(body, dict):
        return _error("invalid_response")
    items = body.get("documents")
    if not isinstance(items, list):
        return _error("invalid_response")
    for entry in items:
        if not isinstance(entry, dict):
            return _error("invalid_response")
        result, doc_type = entry.get("result"), entry.get("document_type")
        if not isinstance(result, str):
            return _error("invalid_response")
        if doc_type is not None and not (isinstance(doc_type, str) and doc_type):
            return _error("invalid_response")
    held = sorted({entry["document_type"] for entry in items
                   if entry["result"] == "ACCEPTED" and entry["document_type"]})
    return ToolResult(OK, {"held_documents": held})


class DocumentServiceGateway:
    """ToolGateway: CheckDocuments over HTTP, every other action the fallback's."""

    def __init__(self, base_url: str, api_key: str, *, fallback: ToolGateway | None = None,
                 transport: Transport = _urllib_transport, timeout: float = TIMEOUT_SECONDS) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._transport, self._timeout = transport, timeout
        self.fallback = fallback if fallback is not None else MockGateway()
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    def __repr__(self) -> str:  # the URL and the key stay out of every log and traceback
        return "DocumentServiceGateway()"

    def idempotent(self, action: str) -> bool:
        return self.fallback.idempotent(action)

    def call(self, action: str, parameters: Mapping[str, Any], idempotency_key: str) -> ToolResult:
        if action != Action.CHECK_DOCUMENTS.value:
            return self.fallback.call(action, parameters, idempotency_key)
        self.calls.append((action, dict(parameters), idempotency_key))
        patient_id = parameters.get("patient_id")
        if not isinstance(patient_id, str) or not patient_id:
            return _error("invalid_request")
        url = f"{self._base_url}/api/v1/patients/{urllib.parse.quote(patient_id, safe='')}/documents"
        headers = {"X-API-Key": self._api_key, "Accept": "application/json"}
        try:
            answer = self._transport("GET", url, headers, None, self._timeout)
        except (OSError, http_client.HTTPException) as exc:
            # OSError: no answer at all (transient). http_client.HTTPException: the server did
            # answer, just not in HTTP the gateway can trust - not the same as no answer at all.
            return no_answer_result(exc)
        return map_documents(answer)


def build_document_gateway(env: Mapping[str, str] | None = None,
                            fallback: ToolGateway | None = None) -> tuple[ToolGateway | None, str]:
    """The gateway the live server uses (design §5.2): (gateway, "mock" | "document-service"),
    or (None, why the Agent Orchestrator must not start). Neither value ever holds the URL or the key."""
    env = os.environ if env is None else env
    url = env.get("DOCUMENT_SERVICE_URL", "").strip()
    if not url:
        return (fallback if fallback is not None else MockGateway()), "mock"
    key = env.get("DOCUMENT_API_KEY", "").strip()
    if not key:
        return None, "disabled: DOCUMENT_API_KEY is not set"
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None, "disabled: DOCUMENT_SERVICE_URL is not an http(s) URL"
    return DocumentServiceGateway(url, key, fallback=fallback), "document-service"
