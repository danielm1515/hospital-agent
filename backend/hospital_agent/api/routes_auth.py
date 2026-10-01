"""/api/auth: the demo IdP's login and the identity of the current token (§18.3, design §3)."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException

from ..auth import Principal, UserStore, auth_secret, authenticate, issue_token
from .deps import current_principal, get_users
from .schemas import Identity, LoginRequest, LoginResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, users: UserStore = Depends(get_users)) -> LoginResponse:
    """The users table, each user's own password hash (§18.3, row 97). An unknown user, an
    inactive one and a wrong password give the same answer, so the table cannot be probed."""
    user = authenticate(users, body.user_id, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid_credentials")
    token = issue_token(user, now=datetime.now(UTC), secret=auth_secret())
    return LoginResponse(token=token, user_id=user.user_id, role=user.role, display_name=user.display_name)


@router.get("/me", response_model=Identity)
def me(principal: Principal = Depends(current_principal)) -> Identity:
    return Identity(user_id=principal.user_id, role=principal.role, display_name=principal.display_name)
