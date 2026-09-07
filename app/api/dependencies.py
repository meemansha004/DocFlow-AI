"""
Shared FastAPI dependencies.

get_current_user — the single auth entry point for HTTP routes: turns a
Bearer token into a fully resolved per-team identity (the same
ResolvedIdentity that GET /auth/me returns), or a 401.
"""

import uuid

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.auth import ResolvedIdentity, resolve_identity, verify_session_token

_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> ResolvedIdentity:
    """
    Verify the ``Authorization: Bearer <token>`` header and return the
    caller's resolved identity. Raises 401 when the header is missing or the
    token is malformed / expired / points at a user that no longer exists.
    """
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        user_id = uuid.UUID(verify_session_token(credentials.credentials))
        return resolve_identity(db, user_id)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
