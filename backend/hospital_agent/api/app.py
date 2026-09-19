"""FastAPI app. In the Core it only reads: /health and the Case Monitor endpoints (design §9).

Every request reads the cases/audit_log rows from Postgres - there is no in-memory
State, so the answers are the same before and after a restart. When the app owns its
engine (a real server, not a test), startup first recovers interrupted executions, then
starts the SLA Worker (Execution design §5) and - when the Model Selector finds an OpenAI
key - the Agent Orchestrator (LLM design §3, §5); /health then reports whether it runs.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from .. import repository
from ..db import make_engine
from ..execution.background import sla_interval_seconds, start_background
from ..execution.gateway import MockGateway
from ..llm.model_selector import llm_version, select_provider
from ..llm.orchestrator import Orchestrator, orchestrator_interval_seconds
from ..naming import State
from ..wiring import build_state_manager
from .schemas import AuditRecord, CaseDetail, CaseSummary


def get_engine(request: Request) -> Engine:
    return request.app.state.engine


def create_app(engine: Engine | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = engine is None
        app.state.engine = make_engine() if owned else engine
        app.state.orchestrator_status = None  # reported by /health only for a real server
        stops, orchestrator = [], None
        if owned:
            provider = select_provider()
            sm = build_state_manager(app.state.engine, llm_version(provider) if provider else None)
            stops.append(start_background(sm, sla_interval_seconds()))
            if provider is None:
                app.state.orchestrator_status = "disabled: OPENAI_API_KEY is not set"
            else:  # the demo's external systems are mocks (spec §18)
                orchestrator = Orchestrator(sm, provider, MockGateway())
                stops.append(orchestrator.run_in_background(orchestrator_interval_seconds()))
                app.state.orchestrator_status = "running"
        yield
        for stop in stops:
            stop.set()
        if orchestrator is not None:
            orchestrator.close()
        if owned:
            app.state.engine.dispose()

    app = FastAPI(title="Hospital Patient Agent", lifespan=lifespan)

    @app.get("/health")
    def health(request: Request, db: Engine = Depends(get_engine)) -> JSONResponse:
        orchestrator = request.app.state.orchestrator_status
        extra = {} if orchestrator is None else {"orchestrator": orchestrator}
        try:
            with db.connect() as conn:
                conn.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return JSONResponse({"status": "degraded", "database": "unavailable", **extra}, status_code=503)
        return JSONResponse({"status": "ok", "database": "ok", **extra})

    @app.get("/cases", response_model=list[CaseSummary])
    def list_cases(state: State | None = None, db: Engine = Depends(get_engine)) -> list[CaseSummary]:
        with db.connect() as conn:
            return [CaseSummary.model_validate(case) for case in repository.list_cases(conn, state)]

    @app.get("/cases/{case_id}", response_model=CaseDetail)
    def get_case(case_id: str, db: Engine = Depends(get_engine)) -> CaseDetail:
        with db.connect() as conn:
            case = repository.load_case(conn, case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="case not found")
        return CaseDetail.model_validate(case)

    @app.get("/cases/{case_id}/audit", response_model=list[AuditRecord])
    def get_audit(case_id: str, db: Engine = Depends(get_engine)) -> list[AuditRecord]:
        with db.connect() as conn:
            if repository.load_case(conn, case_id) is None:
                raise HTTPException(status_code=404, detail="case not found")
            return [AuditRecord.model_validate(entry) for entry in repository.load_trace(conn, case_id)]

    return app


app = create_app()
