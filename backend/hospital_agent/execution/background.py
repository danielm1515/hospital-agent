"""What runs beside the API (Execution design §5): restart recovery once, then the SLA Worker.

Recovery must finish before anything else touches the executions table, so it runs
synchronously at startup; the SLA Worker then ticks on its own daemon thread.
"""
from __future__ import annotations

import os
import threading

from ..state_manager import StateManager
from .recovery import recover
from .sla import DEFAULT_INTERVAL_SECONDS, SlaWorker


def sla_interval_seconds() -> float:
    return float(os.environ.get("SLA_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS))


def start_background(state_manager: StateManager, interval_seconds: float) -> threading.Event:
    """Recover interrupted executions, start the SLA Worker, and return its stop event."""
    recover(state_manager)
    return SlaWorker(state_manager).run_in_background(interval_seconds)
