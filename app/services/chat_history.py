"""
First-party conversation history for the chat agents — backed by our own
`chat_sessions` / `chat_messages` tables (Phase 1 schema, unused until now).

Why this exists alongside Agno's history: Agno keeps its own per-session run
history in the `ai.agent_sessions_runs` table and that is what it feeds back
into the model's context. That store is real and survives a restart, but it
is Agno's schema, keyed only by a session-id string, and not something the
rest of the app queries. `chat_sessions` / `chat_messages` is the canonical,
first-party record: owned by a (user, project), queryable through our models,
and the isolation boundary we actually enforce.

resolve_chat_session() is the guard: a conversation belongs to exactly one
(user_id, project_id). Handing a caller a session that isn't theirs — a
different user, or the right user in the wrong project — raises
SessionScopeError rather than leaking one conversation's history into
another.
"""

import uuid

from sqlalchemy.orm import Session

from app.models.chat import ChatMessage, ChatSession


class SessionScopeError(Exception):
    """A caller asked to continue a chat session that isn't theirs (wrong user or project)."""


def _parse_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def resolve_chat_session(
    db: Session,
    *,
    session_id: str | None,
    user_id: uuid.UUID,
    project_id: uuid.UUID,
    mode: str = "rag",
) -> ChatSession:
    """
    Return the ChatSession for this turn, creating it if needed.

    `mode` ('rag' | 'query') is stamped on a newly created session and tags
    which chat agent owns it. It is NOT part of the ownership check — an
    existing session is returned regardless of its mode as long as it belongs
    to (user_id, project_id).

    - `session_id` is a real, existing session UUID owned by (user_id,
      project_id) -> reuse it.
    - `session_id` is a well-formed UUID with no row yet -> create that exact
      session for (user_id, project_id) (lets a client mint its own id).
    - `session_id` is missing or not a UUID -> start a fresh session with a
      server-assigned id.
    - `session_id` is an existing session owned by someone else, or by this
      user in a different project -> SessionScopeError.

    Flushes so `.session_id` is populated; the caller commits.
    """
    sid = _parse_uuid(session_id)
    if sid is not None:
        existing = db.get(ChatSession, sid)
        if existing is not None:
            if existing.user_id != user_id or existing.project_id != project_id:
                raise SessionScopeError(
                    f"Chat session {sid} does not belong to this user/project."
                )
            return existing
        session = ChatSession(
            session_id=sid, user_id=user_id, project_id=project_id, mode=mode
        )
    else:
        session = ChatSession(user_id=user_id, project_id=project_id, mode=mode)

    db.add(session)
    db.flush()
    return session


def append_message(db: Session, *, session_id: uuid.UUID, role: str, content: str) -> ChatMessage:
    """Append one turn to a session. Caller commits."""
    msg = ChatMessage(session_id=session_id, role=role, content=content or "")
    db.add(msg)
    return msg
