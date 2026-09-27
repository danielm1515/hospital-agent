"""/api/admin: admin_staff's API - sub-project 14's metrics (design
docs/superpowers/specs/2026-09-24-admin-metrics-design.md §5).

Read-only aggregates: no patient_id, case_id or request text ever leaves here (§12.3, design
§11). Viewing is not an Audit event - an audit_log row belongs to a case - so it is logged
here instead, with the viewer and the window only.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.engine import Engine

from .. import metrics
from ..auth import Principal
from .deps import get_engine, require_admin
from .schemas import MetricsResponse

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])
logger = logging.getLogger(__name__)


@router.get("/metrics", response_model=MetricsResponse)
def get_metrics(request: Request, start: str = Query(alias="from"), end: str = Query(alias="to"),
                principal: Principal = Depends(require_admin),
                engine: Engine = Depends(get_engine)) -> MetricsResponse:
    """`from` inclusive, `to` exclusive, both ISO-8601 with a time zone, at most 90 days apart."""
    try:
        window = metrics.Window.parse(start, end)
    except metrics.InvalidWindow as invalid:
        raise HTTPException(status_code=422, detail=invalid.code) from None
    sources = {"appointments": request.app.state.appointments_source,
               "documents": request.app.state.documents_source}
    try:
        result = metrics.compute(engine, window, sources)
    except metrics.MetricsUnavailable:
        logger.warning("metrics unavailable: statement timeout")
        raise HTTPException(status_code=503, detail="metrics_unavailable") from None
    logger.info("metrics viewed by %s for %s .. %s", principal.user_id,
                window.start.isoformat(), window.end.isoformat())
    return MetricsResponse.model_validate(result)
