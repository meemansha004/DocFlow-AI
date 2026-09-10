"""
Per-turn context for the RAG Agent — WHO is asking, and within WHICH project.

Same principle as app/services/session_context.py: the acting user_id and
project_id are established by the caller (CLI startup, or the authenticated
HTTP endpoint) and read internally by the RAG tools. They are NEVER tool
arguments the LLM supplies, so a crafted chat message can't make the agent
retrieve, summarise, or request access as a different user or in another
project.

Unlike session_context (a plain module dict, set once at CLI startup), this
is a ContextVar set/reset around a single agent turn — run_rag_turn() in
app/services/rag_chat.py owns that lifecycle — so concurrent HTTP requests
never see each other's identity.
"""

import contextvars
import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class RagContext:
    user_id: uuid.UUID
    project_id: uuid.UUID


_ctx: contextvars.ContextVar[RagContext | None] = contextvars.ContextVar(
    "rag_agent_context", default=None
)


def set_rag_context(*, user_id: uuid.UUID, project_id: uuid.UUID) -> contextvars.Token:
    return _ctx.set(RagContext(user_id=user_id, project_id=project_id))


def reset_rag_context(token: contextvars.Token) -> None:
    _ctx.reset(token)


def get_rag_context() -> RagContext:
    ctx = _ctx.get()
    if ctx is None:
        raise RuntimeError(
            "No RAG context set — a RAG tool was called outside run_rag_turn()."
        )
    return ctx
