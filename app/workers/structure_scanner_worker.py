"""
Phase 1 §5 — Ingestion -> Structure Scanner handoff.

This is a lightweight polling worker, not a full task queue (Celery/RabbitMQ) —
per the Phase 1 decision, that's unnecessary infra for a prototype and can be
upgraded later if upload volume demands it.

It only implements the HANDOFF CONTRACT here:
  - poll for document_versions with status = 'pending_review'
  - hand each one off to the (not-yet-built) Structure Scanner
  - the scanner's actual scoring/reform logic is a Phase 3 concern —
    process_document() below is a placeholder to be replaced then.

Run this as a standalone process alongside the FastAPI app:
    python -m app.workers.structure_scanner_worker
"""
import time

from sqlalchemy import select

from app.database import SessionLocal
from app.models.document import DocumentVersion, DocumentStatus

POLL_INTERVAL_SECONDS = 5


def process_document(version: DocumentVersion) -> None:
    """
    Placeholder for Phase 3's Structure Scanner logic (score against rubric,
    reform if below threshold, route to human review).

    IMPORTANT (per Phase 1 §5 contract): this must NEVER modify
    version.file_data — the original is always preserved untouched. Any
    reformed content must be written to a separate record.
    """
    print(f"[worker] Picked up version {version.version_id} for scanning (placeholder — Phase 3 TODO)")
    # Phase 3 will replace this stub with real scoring/reform logic, and set
    # status to 'indexed' or 'needs_attention' based on the outcome.


def poll_once() -> int:
    """Runs a single poll cycle. Returns number of documents processed."""
    db = SessionLocal()
    try:
        pending = db.execute(
            select(DocumentVersion)
            .where(DocumentVersion.status == DocumentStatus.pending_review)
            .order_by(DocumentVersion.created_at)
        ).scalars().all()

        for version in pending:
            process_document(version)

        return len(pending)
    finally:
        db.close()


def run_forever() -> None:
    print(f"[worker] Structure Scanner polling worker started (every {POLL_INTERVAL_SECONDS}s)")
    while True:
        count = poll_once()
        if count:
            print(f"[worker] Processed {count} pending document(s)")
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_forever()
