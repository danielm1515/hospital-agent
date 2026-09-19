"""FastAPI app. In the Core it only reads: /health and the Case Monitor endpoints (design §9).

Every request reads the cases/audit_log rows from Postgres - there is no in-memory
State, so the answers are the same before and after a restart.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from .. import repository
from ..db import make_engine
from ..naming import State
from .schemas import AuditRecord, CaseDetail, CaseSummary


def get_engine(request: Request) -> Engine:
    return request.app.state.engine


def create_app(engine: Engine | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = engine is None
        app.state.engine = make_engine() if owned else engine
        yield
        if owned:
            app.state.engine.dispose()

    app = FastAPI(title="Hospital Patient Agent", lifespan=lifespan)

    @app.get("/health")
    def health(db: Engine = Depends(get_engine)) -> JSONResponse:
        try:
            with db.connect() as conn:
                conn.execute(text("SELECT 1"))
        except OperationalError:
            return JSONResponse({"status": "degraded", "database": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok", "database": "ok"})

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
