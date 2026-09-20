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

from ..db import make_engine
from ..execution.background import sla_interval_seconds, start_background
from ..execution.gateway import MockGateway
from ..human_review import HumanReviewService
from ..llm.model_selector import llm_version, select_provider
from ..llm.orchestrator import Orchestrator, orchestrator_interval_seconds
from ..session import SessionService
from ..wiring import build_state_manager
from . import routes_auth, routes_patient, routes_staff
from .deps import get_engine

# The UI's dev server (sub-project 6). CORS_ORIGINS adds any deployed origin.
DEFAULT_CORS_ORIGINS = ("http://localhost:5273", "http://127.0.0.1:5273",
                        "http://localhost:5173", "http://127.0.0.1:5173")


def cors_origins(env: dict[str, str] | None = None) -> list[str]:
    configured = (env or os.environ).get("CORS_ORIGINS", "")
    extra = [origin.strip() for origin in configured.split(",") if origin.strip()]
    return list(DEFAULT_CORS_ORIGINS) + [origin for origin in extra if origin not in DEFAULT_CORS_ORIGINS]


def create_app(engine: Engine | None = None, orchestrator: Orchestrator | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = engine is None
        app.state.engine = make_engine() if owned else engine
        app.state.orchestrator_status = None  # reported by /health only for a real server
        app.state.orchestrator = orchestrator  # injected: stored as it is, never started

        def _wake() -> None:
            running = app.state.orchestrator
            if running is not None:
                running.wake()

        provider = select_provider() if owned else None
        sm = build_state_manager(app.state.engine, llm_version(provider) if provider else None)
        app.state.session = SessionService(sm, wake=_wake)
        app.state.reviews = HumanReviewService(sm, app.state.session, wake=_wake)

        stops, started = [], None
        if owned:
            stops.append(start_background(sm, sla_interval_seconds()))
            if provider is None:
                app.state.orchestrator_status = "disabled: OPENAI_API_KEY is not set"
            else:  # the demo's external systems are mocks (spec §18)
                started = app.state.orchestrator = Orchestrator(sm, provider, MockGateway())
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
        try:
            with db.connect() as conn:
                conn.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return JSONResponse({"status": "degraded", "database": "unavailable", **extra}, status_code=503)
        return JSONResponse({"status": "ok", "database": "ok", **extra})

    app.include_router(routes_auth.router)
    app.include_router(routes_patient.router)
    app.include_router(routes_staff.router)
    return app


app = create_app()
