"""SLA / Timer Worker - the patient's deadline (spec §3.1 PatientSlaExpired, §13.2; Execution design §5).

tick() finds cases waiting for the patient whose deadline has passed and emits
TIMEOUT_EXPIRED with the version it read. The State Manager checks that version
(PatientSlaExpired + the optimistic lock), so an event for a case that has moved on
is discarded. The deadline itself is registered when the case enters
AwaitingPatientInput (MISSING_INFORMATION_DETECTED or a human's new deadline).
"""
from __future__ import annotations

import threading

from .. import repository
from ..naming import Component, Event
from ..state_manager import StateManager, TransitionResult

DEFAULT_INTERVAL_SECONDS = 30.0


class SlaWorker:
    def __init__(self, state_manager: StateManager) -> None:
        self.state_manager = state_manager

    def tick(self) -> list[TransitionResult]:
        sm = self.state_manager
        with sm.engine.connect() as conn:
            expired = repository.expired_patient_deadlines(conn, sm.clock())
        return [
            sm.apply(case.case_id, Event.TIMEOUT_EXPIRED, {"registered_state_version": case.state_version},
                     Component.SLA_WORKER)
            for case in expired
        ]

    def run_in_background(self, interval_seconds: float = DEFAULT_INTERVAL_SECONDS) -> threading.Event:
        """Tick every `interval_seconds` on a daemon thread; set the returned event to stop it."""
        stop = threading.Event()

        def loop() -> None:
            while not stop.wait(interval_seconds):
                self.tick()

        threading.Thread(target=loop, name="sla-worker", daemon=True).start()
        return stop
