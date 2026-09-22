"""Shared HTTP transport for the external-system gateways (sub-project 10 design §2.4; sub-project
13's document-service gateway reuses it unchanged). Never follows a redirect, never uses a proxy,
and never reads more than MAX_BODY_BYTES of a response body.
"""
from __future__ import annotations

import http.client
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass

from .gateway import ERROR, TRANSIENT_FAILURE, ToolResult

# design §2.4 / minor fix 4: the transport never reads more than this many bytes of a body -
# an oversized answer is not the contract either way, so there is no reason to buffer it fully.
# One byte over the 64 KiB limit is enough to tell "too long" apart from "exactly 64 KiB".
MAX_BODY_BYTES = 64 * 1024 + 1


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the caller's credentials to wherever Location points: never follow one."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# design §2.4 / minor fix 3: no proxy, even when HTTP_PROXY/http_proxy is set in the environment -
# a proxy would see the caller's credentials and the patient_id. ProxyHandler({}) is an explicit
# "use no proxy", overriding ProxyHandler's own default of reading those variables.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def request(method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float) -> HttpResponse:
    req = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    try:
        with _OPENER.open(req, timeout=timeout) as answer:
            return HttpResponse(answer.status, answer.read(MAX_BODY_BYTES))
    except urllib.error.HTTPError as exc:  # a status the server did send is an answer, not a failure
        with exc:
            return HttpResponse(exc.code, exc.read(MAX_BODY_BYTES))


def no_answer_result(exc: BaseException) -> ToolResult:
    """What a call that got no usable HTTP answer means (sub-project 10 design §2.6)."""
    if isinstance(exc, http.client.HTTPException):
        return ToolResult(ERROR, {"error": "invalid_response"})
    timed_out = isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError)
    return ToolResult(TRANSIENT_FAILURE, {"error": "timeout" if timed_out else "unavailable"})
