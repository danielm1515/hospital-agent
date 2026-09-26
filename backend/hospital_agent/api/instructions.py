"""Sub-project 18 (design D12, D14): the one read both instruction routes share -
`GET /api/patient/instructions/{source_id}?version=` and its staff counterpart.

Read-only and outside the FSM, exactly like `appointments.py` beside it
(docs/spec_corrections.md row 89, which sub-project 18 extends to cover this read too): nothing
here is proposed, policy-checked, retried, turned into an event or stored. The answer is never
trusted from the appointment-service alone (D12) - the Approved Source Registry, checked
through the real OPA (`instruction_registry.is_approved`, fix round 1 I1), is asked first, and a
source_id/version it does not currently approve is `404 instruction_not_approved` before the
service is ever asked; when OPA itself cannot be asked it is `503 instructions_unavailable`
(Task 8), and the service is not asked then either.
"""
from __future__ import annotations

import logging
import re
import time

from fastapi import HTTPException

from ..instruction_client import InstructionClient, InstructionNotFound, InstructionUnavailable
from ..instruction_registry import is_approved
from .schemas import InstructionView

logger = logging.getLogger(__name__)

# The same id shape source_id/version use everywhere else (api/schemas.py NewRequest,
# appointment_list._ID_RE, execution/appointment_service.py ID_PATTERN).
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _log(level: int, code: str, started: float) -> None:
    """One format for every *service-call* outcome (§12.3): `ok`, `not_found`, an
    InstructionUnavailable code, or `client_error` - never the source_id, the title or the
    text - plus `policy_unavailable` when OPA itself could not be asked (Task 8). The checks
    above `read()`'s call to the client (not configured, a malformed id/version, the registry's
    own denial) need no log line here: they are a fixed, stateless verdict on the request
    itself, not an outcome of asking anything (fix round 1 M4)."""
    logger.log(level, "instruction read: %s in %d ms", code, round((time.perf_counter() - started) * 1000))


def read(client: InstructionClient | None, source_id: str, version: str | None) -> InstructionView:
    # Checked before the id/version pattern, exactly like appointments.read() checks "not
    # enabled" before the window (§9 convention): an unconfigured server answers 404 regardless
    # of what the path or query looks like.
    if client is None:
        raise HTTPException(status_code=404, detail="instructions_not_enabled")
    if version is None or not _ID_RE.fullmatch(source_id) or not _ID_RE.fullmatch(version):
        raise HTTPException(status_code=422, detail="invalid_instruction")
    # The registry, never the service, approves (D12): checked - and denied - before the
    # service is asked at all. `is_approved` asks the real OPA (fix round 1 I1) - no `now` is
    # passed, since policy.rego reads its own clock.
    started = time.perf_counter()
    approved = is_approved(source_id, version)
    if approved is None:
        # Task 8: OPA itself could not be asked. Not "not approved" - that would tell the reader
        # the text is unapproved - but exactly as closed: the service is never asked.
        _log(logging.WARNING, "policy_unavailable", started)
        raise HTTPException(status_code=503, detail="instructions_unavailable")
    if approved is not True:
        raise HTTPException(status_code=404, detail="instruction_not_approved")
    started = time.perf_counter()
    try:
        instruction = client.get(source_id, version)
    except InstructionNotFound:
        # The registry approved this source_id+version, but the service has never heard of it -
        # registry and service disagree, so this is not delivered as though it were approved.
        _log(logging.WARNING, "not_found", started)
        raise HTTPException(status_code=503, detail="instructions_unavailable") from None
    except InstructionUnavailable as failure:
        _log(logging.WARNING, failure.code, started)
        raise HTTPException(status_code=503, detail="instructions_unavailable") from None
    except ValueError:
        # Fix round 1 (I2): InstructionClient.get() itself only catches (OSError,
        # http.client.HTTPException) around the transport call - a client-side ValueError (an
        # API key the transport's own header validation rejects, e.g. one carrying an embedded
        # newline - an operator's configuration mistake, never the caller's fault) would
        # otherwise reach here unhandled and surface as a 500. Fails closed exactly like
        # InstructionUnavailable, and `from None` keeps the underlying exception (which can name
        # the malformed header value) out of the response and its traceback.
        _log(logging.WARNING, "client_error", started)
        raise HTTPException(status_code=503, detail="instructions_unavailable") from None
    _log(logging.INFO, "ok", started)
    return InstructionView.model_validate(instruction)
