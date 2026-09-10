"""
Per-turn context for the Query Agent — WHO is asking, within WHICH project.

Identical principle to app/services/rag_context.py: the acting user_id and
project_id are established by the caller (the authenticated HTTP endpoint) and
read internally by the Query tools. They are NEVER tool arguments the LLM
supplies, so a crafted chat message can't make a metadata lookup run as a
different user or in another project.

A ContextVar set/reset around a single agent turn — run_query_turn() in
app/services/query_chat.py owns that lifecycle — so concurrent HTTP requests
never see each other's identity.
"""

import contextvars
import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class QueryContext:
    user_id: uuid.UUID
    project_id: uuid.UUID


_ctx: contextvars.ContextVar[QueryContext | None] = contextvars.ContextVar(
    "query_agent_context", default=None
)


def set_query_context(*, user_id: uuid.UUID, project_id: uuid.UUID) -> contextvars.Token:
    return _ctx.set(QueryContext(user_id=user_id, project_id=project_id))


def reset_query_context(token: contextvars.Token) -> None:
    _ctx.reset(token)


def get_query_context() -> QueryContext:
    ctx = _ctx.get()
    if ctx is None:
        raise RuntimeError(
            "No Query context set — a Query tool was called outside run_query_turn()."
        )
    return ctx
