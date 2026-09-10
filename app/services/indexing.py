"""
Indexing trigger — combines the two INDEPENDENT status systems that gate
whether a document's current version is ready for RAG indexing:

  - DocumentVersion.status (Scanner-driven: pending_review / needs_attention /
    indexed) — set by document_finalize.py's finalize_document_revision() on
    EVERY finalize, for EVERY stage, regardless of that stage's approval
    policy. `indexed` here means "the Scanner is satisfied" — nothing more.

  - WorkflowState.state (Phase 5, human approval) — exists at all ONLY if the
    document's stage has requires_approval=True. `approved` here means "a
    human signed off" — nothing about content quality.

should_index() is the single place these two axes are combined into an
actual "ready to index" verdict. Call it after any event that could flip
either axis: document_finalize.py's finalize step, and the end of
approve_document() in app/services/workflow.py.

index_document() chunks the document's current version, embeds each chunk
(dense + sparse), and upserts into its tenant's Qdrant collection
(app/services/rag/collection_setup.py) — replacing older points for the
same document_id first, so a re-indexed document is never simultaneously
searchable under two versions.
"""

import logging
import uuid

from qdrant_client import models as qm
from sqlalchemy.orm import Session

from app.models.document import Document, DocumentStatus, DocumentVersion
from app.models.project import Project
from app.models.stage import Stage
from app.models.workflow import WorkflowState, WorkflowStatus
from app.services.rag.chunking import chunk_document
from app.services.rag.collection_setup import (
    DENSE_VECTOR_NAME,
    SPARSE_VECTOR_NAME,
    ensure_tenant_collection,
    get_qdrant_client,
)
from app.services.rag.embedding import embed_dense, embed_sparse

logger = logging.getLogger(__name__)


class DocumentNotIndexableError(Exception):
    pass


def should_index(db: Session, document_id: uuid.UUID) -> bool:
    """
    True iff `document_id`'s CURRENT version is ready for RAG indexing:
      1. DocumentVersion.status == indexed — ALWAYS required.
      2. IF the document's stage has requires_approval=True, ALSO require
         WorkflowState.state == approved.

    A version with status pending_review or needs_attention never satisfies
    this (needs_attention is used for both a failed structural scan and a
    flagged injection scan — neither is ever "indexed").
    """
    document = db.get(Document, document_id)
    if document is None or document.current_version_id is None:
        return False

    version = db.get(DocumentVersion, document.current_version_id)
    if version is None or version.status != DocumentStatus.indexed:
        return False

    stage = db.get(Stage, document.stage_id)
    if stage is not None and stage.requires_approval:
        workflow = (
            db.query(WorkflowState)
            .filter(WorkflowState.document_id == document_id)
            .one_or_none()
        )
        if workflow is None or workflow.state != WorkflowStatus.approved:
            return False

    return True


def _delete_existing_points(client, collection: str, document_id: uuid.UUID) -> None:
    """
    Remove every existing point for this document_id, unconditionally — runs
    even on first index (a harmless no-op then), so a re-indexed document is
    never simultaneously searchable under two versions. document_id isn't a
    payload-indexed field (see collection_setup.py — only tenant_id/
    project_id/stage_id are), so this is a filtered scan, not an indexed
    lookup; fine at our data volumes.
    """
    client.delete(
        collection_name=collection,
        points_selector=qm.FilterSelector(
            filter=qm.Filter(
                must=[qm.FieldCondition(key="document_id", match=qm.MatchValue(value=str(document_id)))]
            )
        ),
    )


def index_document(db: Session, document_id: uuid.UUID) -> dict:
    """
    Chunks the document's CURRENT version content (already-parsed Markdown —
    no re-parsing; see finalize_document_revision/create_document_from_file,
    the only two places DocumentVersion.file_data is ever written), embeds
    each chunk (dense + sparse), and upserts into the tenant's Qdrant
    collection. Deletes any of this document's existing points first.

    Only ever called after should_index() has confirmed the version is
    ready (DocumentVersion.status == indexed, and approved if the stage
    requires it) — this function does not re-check that itself.

    Returns:
        {"collection": str, "chunks_indexed": int, "chunks": [chunk dicts]}

    Raises:
        DocumentNotIndexableError: no current version, or its content isn't
            valid UTF-8 text (shouldn't happen for an `indexed` version).
    """
    document = db.get(Document, document_id)
    if document is None or document.current_version_id is None:
        raise DocumentNotIndexableError(f"Document {document_id} has no current version")

    version = db.get(DocumentVersion, document.current_version_id)
    if version is None:
        raise DocumentNotIndexableError(f"Document {document_id}'s current version is missing")
    try:
        content = version.file_data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DocumentNotIndexableError(
            f"Document {document_id}'s current version is not valid UTF-8 text"
        ) from exc

    project = db.get(Project, document.project_id)
    stage = db.get(Stage, document.stage_id)

    chunks = chunk_document(content)
    if not chunks:
        logger.info("index_document(%s): nothing to index (empty content)", document_id)
        chunks = []

    # Contextual header — baked into the EMBEDDED text only. The stored
    # chunk_text payload field stays the clean original (see chunk_document's
    # own docstring) so it displays cleanly wherever it's read back later.
    embed_texts = [
        f"Project: {project.name if project else ''} | "
        f"Stage: {stage.name if stage else ''} | "
        f"Section: {chunk['section_title']}\n\n{chunk['chunk_text']}"
        for chunk in chunks
    ]

    dense_vectors = embed_dense(embed_texts)
    sparse_vectors = embed_sparse(embed_texts)

    client = get_qdrant_client()
    collection = ensure_tenant_collection(client, document.tenant_id)

    _delete_existing_points(client, collection, document_id)

    if chunks:
        points = [
            qm.PointStruct(
                id=str(uuid.uuid4()),
                vector={
                    DENSE_VECTOR_NAME: dense_vectors[i],
                    SPARSE_VECTOR_NAME: sparse_vectors[i],
                },
                payload={
                    "document_id": str(document_id),
                    "project_id": str(document.project_id),
                    "tenant_id": str(document.tenant_id),
                    "stage_id": str(document.stage_id),
                    "section_title": chunk["section_title"],
                    "chunk_text": chunk["chunk_text"],
                },
            )
            for i, chunk in enumerate(chunks)
        ]
        client.upsert(collection_name=collection, points=points)

    logger.info(
        "index_document(%s): indexed %d chunk(s) into %s", document_id, len(chunks), collection
    )

    return {"collection": collection, "chunks_indexed": len(chunks), "chunks": chunks}
