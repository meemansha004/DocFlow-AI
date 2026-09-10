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

index_document() is a STUB — chunking/embedding into the Qdrant collection
(app/services/rag/collection_setup.py) is Phase B's next piece and is NOT
implemented here. This module only decides WHEN that call should happen.
"""

import logging
import uuid

from sqlalchemy.orm import Session

from app.models.document import Document, DocumentStatus, DocumentVersion
from app.models.stage import Stage
from app.models.workflow import WorkflowState, WorkflowStatus

logger = logging.getLogger(__name__)


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


def index_document(document_id: uuid.UUID) -> None:
    """
    TODO(Phase B — RAG indexing, not yet built): chunk this document's
    current version content and upsert dense+sparse embeddings into its
    tenant's Qdrant collection (app/services/rag/collection_setup.py).

    Stub only — logs so call sites (finalize, approve_document) can be
    wired and tested against should_index() independently of the actual
    chunking/embedding pipeline.
    """
    logger.info(
        "index_document() stub called for document_id=%s — RAG indexing not yet implemented",
        document_id,
    )
