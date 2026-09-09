"""
Phase 2 authentication endpoints.

  POST /auth/signup            email + password -> new User (hashed pw) + token
  POST /auth/login             email + password -> session token
  GET  /auth/google/authorize  -> Google OAuth authorization URL (+ state cookie)
  GET  /auth/google/callback   OAuth callback -> find/create User -> 302 to SPA
  GET  /auth/me                current token -> resolved per-team identity

Not yet wired into access_control.py / session_context.py — that is Phase 3.
"""

import hmac
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.config import DEFAULT_SIGNUP_TENANT_ID
from app.database import get_db
from app.models.tenant import Tenant
from app.models.user import User
from app.services.audit import record_audit
from app.services.auth import (
    ResolvedIdentity,
    create_oauth_state,
    create_session_token,
    exchange_google_code,
    google_authorization_url,
    google_is_configured,
    hash_password,
    oauth_state_cookie_kwargs,
    post_login_redirect_url,
    validate_oauth_state,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_OAUTH_STATE_COOKIE = "google_oauth_state"


# --- request / response models ----------------------------------------------

class SignupRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=256)


class LoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    is_new_user: bool = False


class GoogleAuthorizeResponse(BaseModel):
    authorization_url: str


class TeamMembershipOut(BaseModel):
    team_id: str
    project_id: str
    role: str


class MeResponse(BaseModel):
    user_id: str
    email: str
    tenant_id: str
    is_org_admin: bool
    team_memberships: list[TeamMembershipOut]
    project_admin_project_ids: list[str]


# --- helpers ----------------------------------------------------------------

def _normalize_email(raw: str) -> str:
    email = raw.strip().lower()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise HTTPException(status_code=422, detail="Enter a valid email address")
    return email


def _default_signup_tenant(db: Session) -> uuid.UUID:
    if not DEFAULT_SIGNUP_TENANT_ID:
        raise HTTPException(
            status_code=500,
            detail="DEFAULT_SIGNUP_TENANT_ID is not configured — cannot create accounts",
        )
    try:
        tenant_id = uuid.UUID(DEFAULT_SIGNUP_TENANT_ID)
    except ValueError:
        raise HTTPException(
            status_code=500, detail="DEFAULT_SIGNUP_TENANT_ID is not a valid UUID"
        ) from None
    if db.get(Tenant, tenant_id) is None:
        raise HTTPException(
            status_code=500,
            detail="DEFAULT_SIGNUP_TENANT_ID does not match any tenant",
        )
    return tenant_id


# --- endpoints --------------------------------------------------------------

@router.post("/signup", response_model=TokenResponse, status_code=201)
def signup(body: SignupRequest, db: Session = Depends(get_db)):
    email = _normalize_email(body.email)
    tenant_id = _default_signup_tenant(db)

    if db.execute(select(User).where(User.email == email)).scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="An account with that email already exists")

    user = User(email=email, tenant_id=tenant_id, password_hash=hash_password(body.password))
    db.add(user)
    db.flush()
    record_audit(db, actor_id=user.user_id, action="SIGNUP", resource_type="user",
                 resource_id=user.user_id, details={"method": "password"})
    db.commit()
    db.refresh(user)

    return TokenResponse(
        access_token=create_session_token(str(user.user_id)), is_new_user=True
    )


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None or not user.password_hash or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    # LOGIN events are deliberately not audited (finalized Audit Log design —
    # the log carries account/permission-management actions only).
    return TokenResponse(access_token=create_session_token(str(user.user_id)))


@router.get("/google/authorize", response_model=GoogleAuthorizeResponse)
def google_authorize(request: Request, response: Response):
    if not google_is_configured():
        raise HTTPException(status_code=503, detail="Google sign-in is not configured")
    state = create_oauth_state()
    response.set_cookie(_OAUTH_STATE_COOKIE, state, **oauth_state_cookie_kwargs(request))
    return GoogleAuthorizeResponse(authorization_url=google_authorization_url(state))


@router.get("/google/callback")
def google_callback(code: str, state: str, request: Request, db: Session = Depends(get_db)):
    cookie_state = request.cookies.get(_OAUTH_STATE_COOKIE)
    if (
        not cookie_state
        or not validate_oauth_state(state)
        or not hmac.compare_digest(state, cookie_state)
    ):
        raise HTTPException(status_code=400, detail="Invalid Google sign-in state")

    try:
        identity = exchange_google_code(code)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    email = identity["email"].strip().lower()
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    is_new_user = user is None
    if user is None:
        tenant_id = _default_signup_tenant(db)
        user = User(email=email, tenant_id=tenant_id, password_hash=None)
        db.add(user)
        db.flush()
        record_audit(db, actor_id=user.user_id, action="SIGNUP", resource_type="user",
                     resource_id=user.user_id, details={"method": "google"})
    # LOGIN events are deliberately not audited (see POST /auth/login).
    db.commit()
    db.refresh(user)

    token = create_session_token(str(user.user_id))
    redirect = RedirectResponse(
        post_login_redirect_url(token, is_new_user=is_new_user), status_code=302
    )
    redirect.delete_cookie(_OAUTH_STATE_COOKIE, path="/")
    return redirect


@router.get("/me", response_model=MeResponse)
def me(identity: ResolvedIdentity = Depends(get_current_user)):
    return MeResponse(
        user_id=str(identity.user_id),
        email=identity.email,
        tenant_id=str(identity.tenant_id),
        is_org_admin=identity.is_org_admin,
        team_memberships=[
            TeamMembershipOut(
                team_id=str(m.team_id),
                project_id=str(m.project_id),
                role=m.role.value,
            )
            for m in identity.team_memberships
        ],
        project_admin_project_ids=[str(pid) for pid in identity.project_admin_project_ids],
    )
