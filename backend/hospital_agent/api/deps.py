"""FastAPI dependencies: who is calling, and the services the app built (design §6).

The identity comes from the Authorization header only - never from the request body or a
query parameter (§18.3). A route that needs a role depends on require_patient or
require_staff, so a missing check is a missing dependency, not a forgotten `if`.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request
from sqlalchemy.engine import Engine

from ..auth import ADMIN_STAFF, Principal, UserStore, auth_secret, verify_token
from ..human_review import HumanReviewService
from ..session import SessionService

BEARER = "bearer"


def get_engine(request: Request) -> Engine:
    return request.app.state.engine


def get_session(request: Request) -> SessionService:
    return request.app.state.session


def get_reviews(request: Request) -> HumanReviewService:
    return request.app.state.reviews


def get_users(request: Request) -> UserStore:
    return request.app.state.users


def current_principal(request: Request) -> Principal:
    """The verified token's Principal, or 401. Every failure gives the same answer: a wrong
    password, an expired token and a forged signature must not be told apart."""
    scheme, _, token = (request.headers.get("Authorization") or "").partition(" ")
    principal = None
    if scheme.lower() == BEARER and token.strip():
        principal = verify_token(token.strip(), now=datetime.now(UTC), secret=auth_secret(),
                                 store=get_users(request))
    if principal is None:
        raise HTTPException(status_code=401, detail="not_authenticated",
                            headers={"WWW-Authenticate": "Bearer"})
    return principal


def require_patient(principal: Principal = Depends(current_principal)) -> Principal:
    if principal.is_staff:
        raise HTTPException(status_code=403, detail="patients_only")
    return principal


def require_staff(principal: Principal = Depends(current_principal)) -> Principal:
    if not principal.is_staff:
        raise HTTPException(status_code=403, detail="staff_only")
    return principal


def require_admin(principal: Principal = Depends(current_principal)) -> Principal:
    """Sub-project 14: the metrics screen is admin_staff's only (design §5)."""
    if principal.role != ADMIN_STAFF:
        raise HTTPException(status_code=403, detail="admin_only")
    return principal
