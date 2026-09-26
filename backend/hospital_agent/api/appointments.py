"""Sub-project 16 (design D5, D6, D9): the window and the one read both appointment routes share."""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException

from ..appointment_list import AppointmentListClient, AppointmentsUnavailable, PatientNotFound
from .schemas import AppointmentsView, AppointmentView

DEFAULT_SPAN = timedelta(days=30)
MAX_SPAN = timedelta(days=366)
logger = logging.getLogger(__name__)


def _instant(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid_range") from None
    if parsed.tzinfo is None:
        raise HTTPException(status_code=422, detail="invalid_range")
    try:
        return parsed.astimezone(UTC)  # a fixed instant either way; UTC is what the answer echoes back
    except OverflowError:
        # A year at the edge of datetime's range (e.g. 9999) can convert to an offset that no
        # longer fits (`astimezone` shifts by the offset before it can be rejected as
        # out-of-range) - fails closed as the same 422, not a 500.
        raise HTTPException(status_code=422, detail="invalid_range") from None


def window(start: str | None, end: str | None, now: datetime) -> tuple[datetime, datetime]:
    """`from` inclusive, `to` exclusive. Neither: now .. now+30d; one: 30 days from/to it."""
    low = _instant(start) if start is not None else None
    high = _instant(end) if end is not None else None
    if low is None and high is None:
        low = now
    try:
        if high is None:
            high = low + DEFAULT_SPAN
        if low is None:
            low = high - DEFAULT_SPAN
    except OverflowError:
        # The default 30-day span pushed past datetime's range from a bound already at its
        # edge (e.g. `from=9999-12-31...` alone) - the same 422, never a 500.
        raise HTTPException(status_code=422, detail="invalid_range") from None
    if not low < high or high - low > MAX_SPAN:
        raise HTTPException(status_code=422, detail="invalid_range")
    return low, high


def _log(level: int, code: str, started: float) -> None:
    """One format for every outcome (§12.3): the code only - `ok`, `patient_not_found`, an
    AppointmentsUnavailable code, or `client_error` - never the patient or an appointment."""
    logger.log(level, "appointment list: %s in %d ms", code, round((time.perf_counter() - started) * 1000))


def read(client: AppointmentListClient | None, patient_id: str, start: str | None,
        end: str | None) -> AppointmentsView:
    # Checked before the window: an unconfigured server answers 404 regardless of what the
    # query looks like - there is no window to be wrong about if nothing can answer it anyway.
    if client is None:
        raise HTTPException(status_code=404, detail="appointments_not_enabled")
    low, high = window(start, end, datetime.now(UTC))
    started = time.perf_counter()
    try:
        result = client.list(patient_id, low, high)
    except PatientNotFound:
        _log(logging.INFO, "patient_not_found", started)
        raise HTTPException(status_code=404, detail="patient_not_found") from None
    except AppointmentsUnavailable as failure:
        _log(logging.WARNING, failure.code, started)
        raise HTTPException(status_code=503, detail="appointments_unavailable") from None
    except ValueError:
        # AppointmentListClient.list() raises this for a naive start/end, a patient_id that
        # does not fully match its pattern, or the client itself misconfigured (e.g. an
        # invalid API key header the transport rejects before a request is even sent). The
        # first two are not reachable through these routes (they always resolve patient_id from
        # the token or the case, and window() always returns aware datetimes); the third is an
        # operator's configuration mistake - never the patient's fault. Fail closed exactly like AppointmentsUnavailable rather than a 500,
        # and never name the patient_id in the log (§12.3).
        _log(logging.WARNING, "client_error", started)
        raise HTTPException(status_code=503, detail="appointments_unavailable") from None
    _log(logging.INFO, "ok", started)
    # appointment_at is already normalised to UTC by the client (appointment_list._appointment),
    # the one place that can see - and safely reject - a row whose own conversion overflows.
    appointments = [AppointmentView.model_validate(a) for a in result.appointments]
    return AppointmentsView(window_from=low, window_to=high, truncated=result.truncated, appointments=appointments)
