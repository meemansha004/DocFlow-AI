"""
Qdrant collection setup for RAG (Phase A infrastructure).

One collection per tenant: `tenant_{tenant_id}`. Each point carries:
  - a named DENSE vector "dense"  (BAAI/bge-small-en-v1.5, 384-dim, cosine)
  - a named SPARSE vector "sparse" (Qdrant BM25 — IDF-modified sparse vectors,
    computed client-side by fastembed's SparseTextEmbedding("Qdrant/bm25"))

Multi-tenancy below the collection is via PAYLOAD FILTERING, not separate
collections: every point carries tenant_id, project_id, stage_id in its
payload, and those three fields are the only ones indexed for fast filtering.

Deliberately NOT indexed here: sensitivity_level, team_id, or any other
access-control field. Those exist on the source Document row in Postgres,
not as Qdrant filters — ABAC enforcement stays in the app layer (Phase B/C
resolves the allowed stage_ids/document_ids there and passes them down),
not baked into the vector store's payload index. Keeping the vector store
attribute-blind avoids two ABAC implementations drifting apart.

Nothing consumes this yet — Phase C retrieval will build embeddings and
search on top of it. Built and tested now.
"""

import uuid
from functools import lru_cache

from qdrant_client import QdrantClient, models

from app.config import QDRANT_API_KEY, QDRANT_LOCAL_PATH, QDRANT_URL

DENSE_VECTOR_NAME = "dense"
DENSE_VECTOR_SIZE = 384  # BAAI/bge-small-en-v1.5
SPARSE_VECTOR_NAME = "sparse"

# Payload fields every point carries and that filtering is scoped to.
# Keep in sync with the payload indexes created below.
INDEXED_PAYLOAD_FIELDS = ("tenant_id", "project_id", "stage_id", "document_id")


@lru_cache(maxsize=1)
def get_qdrant_client() -> QdrantClient:
    """
    QDRANT_URL blank -> embedded local mode: on-disk at QDRANT_LOCAL_PATH,
    no server, no key. QDRANT_URL set -> connect to that Qdrant instance.

    Cached as a process-wide singleton — this is not just an optimization.
    Embedded/local-mode Qdrant file-locks its storage directory and refuses
    a second concurrent open, even from the SAME process ("Storage folder
    ... is already accessed by another instance of Qdrant client"). A fresh
    QdrantClient(path=...) on every call would work in isolation but break
    the moment anything else in the process (a script, a later request)
    still holds an earlier one open. One shared client avoids that entirely
    and is the documented pattern for embedded mode. Server mode (QDRANT_URL
    set) has no such constraint, but sharing one client there is still
    correct and cheaper than reconnecting per call.
    """
    if QDRANT_URL:
        return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None)
    return QdrantClient(path=QDRANT_LOCAL_PATH)


def collection_name_for_tenant(tenant_id: uuid.UUID) -> str:
    return f"tenant_{tenant_id}"


def ensure_tenant_collection(client: QdrantClient, tenant_id: uuid.UUID) -> str:
    """
    Create this tenant's collection (dense + sparse vector config, payload
    indexes on tenant_id/project_id/stage_id) if it doesn't already exist.
    Idempotent. Returns the collection name.
    """
    name = collection_name_for_tenant(tenant_id)

    if not client.collection_exists(name):
        client.create_collection(
            collection_name=name,
            vectors_config={
                DENSE_VECTOR_NAME: models.VectorParams(
                    size=DENSE_VECTOR_SIZE,
                    distance=models.Distance.COSINE,
                ),
            },
            sparse_vectors_config={
                SPARSE_VECTOR_NAME: models.SparseVectorParams(
                    modifier=models.Modifier.IDF,
                ),
            },
        )

    existing_indexes = set(
        client.get_collection(name).payload_schema.keys()
    )
    for field in INDEXED_PAYLOAD_FIELDS:
        if field in existing_indexes:
            continue
        client.create_payload_index(
            collection_name=name,
            field_name=field,
            field_schema=models.PayloadSchemaType.KEYWORD,
        )

    return name
