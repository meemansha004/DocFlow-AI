"""
Phase C retrieval — the RAG pipeline up through reranking. Generation and
conversation history are separate, later pieces; this module only answers
"which chunks, in what order, is this user actually allowed to see for
this query."

Pipeline (retrieve()):
  1. Zero-access check — hard reject before any Qdrant call.
  2. Stage scope resolution — resolve_stage_scope() (Phase A Part 1)
     INTERSECTED with get_accessible_stages_for_user() (Phase A Part 3). A
     referenced stage the user's team can't access never enters scope.
  3. Coarse Qdrant hybrid search (dense + sparse, RRF-fused), filtered to
     tenant/project/resolved-stages -> COARSE_LIMIT raw candidates.
  4. Live per-candidate-document Postgres access check via
     access_control.classify_document_visibility() (reused, not
     reimplemented) -> fully_allowed / blocked_by_sensitivity / not_visible.
  5. Keep only fully_allowed chunks. Track which documents were
     blocked_by_sensitivity (for a later "request access" suggestion —
     built elsewhere, not here). not_visible is dropped with no trace.
  6. Cross-encoder rerank of the ENTIRE fully_allowed survivor set (not a
     pre-truncated top-k) — this produces the actual final ordering.
  7. Relevance floor applied AFTER reranking (see reranking.py).
  8. Final top TOP_K chunks.
"""

import uuid
from dataclasses import dataclass, field

from qdrant_client import models as qm
from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.project import Project
from app.services.access_control import (
    DocumentVisibility,
    classify_document_visibility,
    get_accessible_stages_for_user,
    has_any_project_access,
)
from app.services.rag.collection_setup import (
    DENSE_VECTOR_NAME,
    SPARSE_VECTOR_NAME,
    collection_name_for_tenant,
    ensure_tenant_collection,
    get_qdrant_client,
)
from app.services.rag.embedding import embed_dense, embed_sparse
from app.services.rag.reranking import RELEVANCE_FLOOR, rerank_scores
from app.services.rag.stage_scope import resolve_stage_scope

COARSE_LIMIT = 50
TOP_K = 6


class NoProjectAccessError(Exception):
    """Raised by retrieve() when the user has no relationship to the project at all."""


@dataclass
class RetrievedChunk:
    document_id: uuid.UUID
    section_title: str
    chunk_text: str
    stage_id: uuid.UUID
    score: float  # final cross-encoder relevance score


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk] = field(default_factory=list)
    # True if ANY candidate document was blocked_by_sensitivity for this
    # user (visible team, insufficient clearance) — generation (a later
    # piece) uses this to offer a "request access" suggestion. The
    # documents themselves are tracked too, for whenever that's built.
    blocked_by_sensitivity: bool = False
    blocked_document_ids: list[uuid.UUID] = field(default_factory=list)


def _resolve_scope(db: Session, user_id: uuid.UUID, project_id: uuid.UUID, stage_id: uuid.UUID | None) -> set[uuid.UUID]:
    """
    INTERSECTS resolve_stage_scope() with get_accessible_stages_for_user() —
    never just one or the other. A stage `stage_id` references that the
    user's team(s) can't access is excluded, even though resolve_stage_scope
    on its own would include it.
    """
    accessible = set(get_accessible_stages_for_user(db, user_id, project_id))
    if stage_id is None:
        return accessible
    raw_scope = set(resolve_stage_scope(db, stage_id))
    return raw_scope & accessible


def _coarse_search(
    client, collection: str, *, tenant_id: uuid.UUID, project_id: uuid.UUID,
    stage_ids: set[uuid.UUID], query: str, limit: int = COARSE_LIMIT,
) -> list:
    """RRF-fused hybrid (dense + sparse) search, scoped by payload filter. Returns Qdrant ScoredPoints, RRF order."""
    dense_vec = embed_dense([query])[0]
    sparse_vec = embed_sparse([query])[0]

    payload_filter = qm.Filter(
        must=[
            qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=str(tenant_id))),
            qm.FieldCondition(key="project_id", match=qm.MatchValue(value=str(project_id))),
            qm.FieldCondition(key="stage_id", match=qm.MatchAny(any=[str(s) for s in stage_ids])),
        ]
    )

    response = client.query_points(
        collection_name=collection,
        prefetch=[
            qm.Prefetch(query=dense_vec, using=DENSE_VECTOR_NAME, filter=payload_filter, limit=limit),
            qm.Prefetch(query=sparse_vec, using=SPARSE_VECTOR_NAME, filter=payload_filter, limit=limit),
        ],
        query=qm.FusionQuery(fusion=qm.Fusion.RRF),
        query_filter=payload_filter,
        limit=limit,
        with_payload=True,
    )
    return response.points


def _access_filter_candidates(
    db: Session, user_id: uuid.UUID, points: list,
) -> tuple[list, list[uuid.UUID]]:
    """
    Live per-candidate-DOCUMENT access check (one classify call per unique
    document_id among the candidates, not per chunk). Returns
    (fully_allowed_points, blocked_by_sensitivity_document_ids).
    """
    doc_ids = {uuid.UUID(p.payload["document_id"]) for p in points}
    documents = {d.document_id: d for d in db.query(Document).filter(Document.document_id.in_(doc_ids)).all()}

    outcome_by_doc: dict[uuid.UUID, DocumentVisibility] = {}
    for doc_id, document in documents.items():
        outcome_by_doc[doc_id] = classify_document_visibility(db, user_id, document)

    allowed = []
    blocked_ids: list[uuid.UUID] = []
    for p in points:
        doc_id = uuid.UUID(p.payload["document_id"])
        outcome = outcome_by_doc.get(doc_id, DocumentVisibility.not_visible)
        if outcome == DocumentVisibility.fully_allowed:
            allowed.append(p)
        elif outcome == DocumentVisibility.blocked_by_sensitivity:
            if doc_id not in blocked_ids:
                blocked_ids.append(doc_id)
        # not_visible: silently dropped, no trace.

    return allowed, blocked_ids


def _rerank(query: str, points: list) -> list[RetrievedChunk]:
    """
    Cross-encoder rerank of the FULL candidate set passed in (never
    pre-truncated), floor applied after, sorted descending by score.
    """
    if not points:
        return []

    texts = [p.payload["chunk_text"] for p in points]
    scores = rerank_scores(query, texts)

    chunks = [
        RetrievedChunk(
            document_id=uuid.UUID(p.payload["document_id"]),
            section_title=p.payload["section_title"],
            chunk_text=p.payload["chunk_text"],
            stage_id=uuid.UUID(p.payload["stage_id"]),
            score=score,
        )
        for p, score in zip(points, scores)
    ]
    chunks.sort(key=lambda c: c.score, reverse=True)
    return [c for c in chunks if c.score > RELEVANCE_FLOOR]


def retrieve(
    db: Session, user_id: uuid.UUID, project_id: uuid.UUID, query: str, stage_id: uuid.UUID | None = None,
) -> RetrievalResult:
    """
    See module docstring for the full pipeline. Raises NoProjectAccessError
    for a user with no relationship to the project — before any Qdrant call.
    """
    if not has_any_project_access(db, user_id, project_id):
        raise NoProjectAccessError(f"User {user_id} has no access to project {project_id}")

    scope = _resolve_scope(db, user_id, project_id, stage_id)
    if not scope:
        return RetrievalResult()  # nothing accessible in scope — legitimate empty result, not an error

    project = db.get(Project, project_id)
    tenant_id = project.tenant_id

    client = get_qdrant_client()
    collection = ensure_tenant_collection(client, tenant_id)

    coarse_points = _coarse_search(
        client, collection, tenant_id=tenant_id, project_id=project_id, stage_ids=scope, query=query,
    )
    if not coarse_points:
        return RetrievalResult()

    allowed_points, blocked_ids = _access_filter_candidates(db, user_id, coarse_points)

    final_chunks = _rerank(query, allowed_points)[:TOP_K]

    return RetrievalResult(
        chunks=final_chunks,
        blocked_by_sensitivity=bool(blocked_ids),
        blocked_document_ids=blocked_ids,
    )
