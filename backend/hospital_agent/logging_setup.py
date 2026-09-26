"""Configures `hospital_agent`'s own loggers, once, for a real server.

Uvicorn configures only its own loggers (`uvicorn`, `uvicorn.access`, ...) - the root logger
is left at its default level (WARNING) with no handler of its own. Every `logger.info(...)`
anywhere under `hospital_agent.*` was therefore silently dropped on the live server: not just
`llm/telemetry.py`'s `outcome=ok` line (staff-fixes design Task 1), but existing lines too,
e.g. `session.py`'s `"pdf reply: unreadable"`.

`configure()` is called only from the owned-app path of `api.app.create_app` (a real server,
never a test - a test always injects an engine, so `create_app(app_engine)` never calls
this), so it never runs during `pytest` and never touches `caplog`'s own capture.
"""
from __future__ import annotations

import logging

LOGGER_NAME = "hospital_agent"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure() -> None:
    """INFO level, one stderr StreamHandler, no propagation to the root logger (so nothing
    is logged twice if the root ever gets a handler of its own). Idempotent: a second call
    (e.g. a reload) finds the marker already set and adds no second handler."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if getattr(logger, "_hospital_agent_configured", False):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(handler)
    logger._hospital_agent_configured = True  # type: ignore[attr-defined]
