"""The FastAPI app: /health (public) and the authenticated /api (design §6).

Every request reads the cases/audit_log rows from Postgres - there is no in-memory State,
so the answers are the same before and after a restart. The lifespan builds the State
Manager, the Session Service and the Human Review Service; when the app owns its engine (a
real server, not a test) it also recovers interrupted executions, starts the SLA Worker
(Execution design §5) and - when the Model Selector finds an OpenAI key - the Agent
Orchestrator (LLM design §3, §5). /health then reports whether it runs.

A test injects both the engine and the Orchestrator: an injected Orchestrator is stored as
it is and never started, so the test steps it itself. Either way _wake() is what a patient
or staff action calls, so a case moves the moment its event commits.
"""
from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from ..appointment_list import AppointmentListClient, build_list_client
from ..db import make_engine
from ..document_intake import build_intake_client
from ..execution.appointment_service import build_gateway
from ..execution.background import sla_interval_seconds, start_background
from ..execution.document_service import build_document_gateway
from ..human_review import HumanReviewService
from ..llm.model_selector import llm_version, select_provider
from ..llm.orchestrator import Orchestrator, orchestrator_interval_seconds
from ..session import DocumentIntake, SessionService
from ..wiring import build_state_manager
from . import routes_admin, routes_auth, routes_patient, routes_staff
from .deps import get_engine
from .routes_patient import UPLOAD_BODY_LIMIT

__all__ = ["UPLOAD_BODY_LIMIT", "UploadSizeLimit", "app", "cors_origins", "create_app"]

# The UI's dev server (sub-project 6). CORS_ORIGINS adds any deployed origin.
DEFAULT_CORS_ORIGINS = ("http://localhost:5273", "http://127.0.0.1:5273",
                        "http://localhost:5173", "http://127.0.0.1:5173")


def cors_origins(env: dict[str, str] | None = None) -> list[str]:
    configured = (env or os.environ).get("CORS_ORIGINS", "")
    extra = [origin.strip() for origin in configured.split(",") if origin.strip()]
    return list(DEFAULT_CORS_ORIGINS) + [origin for origin in extra if origin not in DEFAULT_CORS_ORIGINS]


class UploadSizeLimit:
    """Pure ASGI: a PDF upload (POST .../documents/file or POST .../reply/file) must say how big
    it is, and may be at most UPLOAD_BODY_LIMIT - refused by its Content-Length alone, before the
    body is read or any route runs (411 length_required, 413 too_large; `{"detail": code}` like
    the rest of the API). Every other request passes untouched."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope["method"] == "POST" \
                and scope["path"].endswith(("/documents/file", "/reply/file")):
            raw = dict(scope.get("headers") or []).get(b"content-length")
            if raw is None or not raw.isdigit():  # missing, empty, signed, or not a number at all
                await self._refuse(send, 411, "length_required")
                return
            # Compared as digits first: int() of thousands of digits would raise (Python's
            # int-to-str limit) - and any length that long is over the limit anyway.
            digits = raw.lstrip(b"0") or b"0"
            if len(digits) > len(str(UPLOAD_BODY_LIMIT)) or int(digits) > UPLOAD_BODY_LIMIT:
                await self._refuse(send, 413, "too_large")
                return
        await self.app(scope, receive, send)

    @staticmethod
    async def _refuse(send, status: int, code: str) -> None:
        body = json.dumps({"detail": code}).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode()),
                                (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": body})


def create_app(engine: Engine | None = None, orchestrator: Orchestrator | None = None,
               document_intake: DocumentIntake | None = None,
               appointment_list: AppointmentListClient | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = engine is None
        app.state.engine = make_engine() if owned else engine
        app.state.orchestrator_status = None  # reported by /health only for a real server
        app.state.appointments_source = None  # likewise: "mock" or "appointment-service"
        app.state.documents_source = None  # likewise: "mock" or "document-service"
        app.state.orchestrator = orchestrator  # injected: stored as it is, never started
        # Sub-project 16: the patient's own appointment-list read (routes_patient/routes_staff),
        # separate from CheckAppointment's gateway above - a test injects its own fake the way
        # it injects the Orchestrator.
        app.state.appointment_list = build_list_client() if owned else appointment_list

        def _wake() -> None:
            running = app.state.orchestrator
            if running is not None:
                running.wake()

        provider = select_provider() if owned else None
        sm = build_state_manager(app.state.engine, llm_version(provider) if provider else None)
        # Sub-project 13: the patient's PDF goes to the document-service when it is configured;
        # a test injects its own client (or none) the way it injects the Orchestrator.
        app.state.session = SessionService(
            sm, wake=_wake, document_intake=build_intake_client() if owned else document_intake)
        app.state.reviews = HumanReviewService(sm, app.state.session, wake=_wake)

        stops, started = [], None
        if owned:
            stops.append(start_background(sm, sla_interval_seconds()))
            if provider is None:
                app.state.orchestrator_status = "disabled: OPENAI_API_KEY is not set"
            else:
                # The demo's external systems are mocks (spec §18); on the owner's stack
                # CheckAppointment may ask the real appointment-service (sub-project 10) and
                # CheckDocuments the real document-service (sub-project 13) - the appointment
                # gateway falls back to the document gateway, which falls back to the mock.
                documents_gateway, documents_source = build_document_gateway()
                gateway, source = (None, documents_source) if documents_gateway is None \
                    else build_gateway(fallback=documents_gateway)
                if gateway is None:
                    app.state.orchestrator_status = source
                else:
                    app.state.appointments_source = source
                    app.state.documents_source = documents_source
                    started = app.state.orchestrator = Orchestrator(sm, provider, gateway)
                    stops.append(started.run_in_background(orchestrator_interval_seconds()))
                    app.state.orchestrator_status = "running"
        yield
        for stop in stops:
            stop.set()
        if started is not None:
            started.close()
        if owned:
            app.state.engine.dispose()

    app = FastAPI(title="Hospital Patient Agent", lifespan=lifespan)
    app.add_middleware(UploadSizeLimit)  # added first, so CORS stays the outermost layer
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins(),
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.exception_handler(RequestValidationError)
    async def invalid_body(request: Request, exc: RequestValidationError) -> JSONResponse:
        """§12.3: an error body is a code, never data. FastAPI's own validation body echoes
        the value that failed (`input`), which would put a request text, a document or a
        password into the response - and from there into a browser console or a log."""
        return JSONResponse({"detail": "invalid_body"}, status_code=422)

    @app.get("/health")
    def health(request: Request, db: Engine = Depends(get_engine)) -> JSONResponse:
        orchestrator_status = request.app.state.orchestrator_status
        extra = {} if orchestrator_status is None else {"orchestrator": orchestrator_status}
        if request.app.state.appointments_source is not None:
            extra["appointments"] = request.app.state.appointments_source
        if request.app.state.documents_source is not None:
            extra["documents"] = request.app.state.documents_source
        try:
            with db.connect() as conn:
                conn.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return JSONResponse({"status": "degraded", "database": "unavailable", **extra}, status_code=503)
        return JSONResponse({"status": "ok", "database": "ok", **extra})

    app.include_router(routes_auth.router)
    app.include_router(routes_patient.router)
    app.include_router(routes_staff.router)
    app.include_router(routes_admin.router)
    return app


app = create_app()
