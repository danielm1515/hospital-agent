"""/api/auth: the demo IdP's login and the identity of the current token (§18.3, design §3)."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException

from ..auth import Principal, auth_secret, authenticate, issue_token
from .deps import current_principal
from .schemas import Identity, LoginRequest, LoginResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest) -> LoginResponse:
    """The fixed demo user list and one shared password (§18.3). An unknown user and a wrong
    password give the same answer, so the list cannot be probed."""
    user = authenticate(body.user_id, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid_credentials")
    token = issue_token(user, now=datetime.now(UTC), secret=auth_secret())
    return LoginResponse(token=token, user_id=user.user_id, role=user.role, display_name=user.display_name)


@router.get("/me", response_model=Identity)
def me(principal: Principal = Depends(current_principal)) -> Identity:
    return Identity(user_id=principal.user_id, role=principal.role, display_name=principal.display_name)
