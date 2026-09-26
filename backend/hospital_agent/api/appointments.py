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
    return parsed.astimezone(UTC)  # a fixed instant either way; UTC is what the answer echoes back


def window(start: str | None, end: str | None, now: datetime) -> tuple[datetime, datetime]:
    """`from` inclusive, `to` exclusive. Neither: now .. now+30d; one: 30 days from/to it."""
    low = _instant(start) if start is not None else None
    high = _instant(end) if end is not None else None
    if low is None and high is None:
        low = now
    if high is None:
        high = low + DEFAULT_SPAN
    if low is None:
        low = high - DEFAULT_SPAN
    if not low < high or high - low > MAX_SPAN:
        raise HTTPException(status_code=422, detail="invalid_range")
    return low, high


def read(client: AppointmentListClient | None, patient_id: str, start: str | None,
        end: str | None) -> AppointmentsView:
    low, high = window(start, end, datetime.now(UTC))
    if client is None:
        raise HTTPException(status_code=404, detail="appointments_not_enabled")
    started = time.perf_counter()
    try:
        result = client.list(patient_id, low, high)
    except PatientNotFound:
        logger.info("appointment list: patient_not_found")
        raise HTTPException(status_code=404, detail="patient_not_found") from None
    except AppointmentsUnavailable as failure:
        logger.warning("appointment list unavailable: %s", failure.code)
        raise HTTPException(status_code=503, detail="appointments_unavailable") from None
    except ValueError:
        # AppointmentListClient.list() raises this for a naive start/end or a patient_id that
        # does not fully match its pattern - both programming errors (the routes always
        # resolve patient_id from the token or the case, and window() always returns aware
        # datetimes), never the client's fault. Fail closed exactly like AppointmentsUnavailable
        # rather than a 500, and never name the patient_id in the log (§12.3).
        logger.warning("appointment list unavailable: invalid_patient_id")
        raise HTTPException(status_code=503, detail="appointments_unavailable") from None
    logger.info("appointment list: %d rows in %d ms", len(result.appointments),
                round((time.perf_counter() - started) * 1000))
    return AppointmentsView(window_from=low, window_to=high, truncated=result.truncated,
                            appointments=[AppointmentView.model_validate(a) for a in result.appointments])
