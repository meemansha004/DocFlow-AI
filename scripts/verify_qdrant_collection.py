"""
Phase A Part 2 verification: creates the real per-tenant Qdrant collection
(embedded local mode) for our actual seeded tenant and prints get_collection()
so the dense (384-dim, cosine) + sparse (IDF) vector config and the
tenant_id/project_id/stage_id-only payload indexes can be eyeballed.

Run from repo root: PYTHONPATH=. python scripts/verify_qdrant_collection.py
"""

import uuid

from app.config import QDRANT_URL
from app.database import SessionLocal
from app.models.tenant import Tenant
from app.services.rag.collection_setup import (
    DENSE_VECTOR_NAME,
    INDEXED_PAYLOAD_FIELDS,
    SPARSE_VECTOR_NAME,
    collection_name_for_tenant,
    ensure_tenant_collection,
    get_qdrant_client,
)


def main() -> None:
    db = SessionLocal()
    tenant = db.query(Tenant).first()
    if tenant is None:
        raise SystemExit("No seeded tenant found — nothing to create a collection for.")
    db.close()

    print("=" * 78)
    print(f" Seeded tenant: {tenant.tenant_id}  ({tenant.name})")
    print("=" * 78)

    client = get_qdrant_client()
    name = ensure_tenant_collection(client, tenant.tenant_id)
    assert name == collection_name_for_tenant(tenant.tenant_id)
    print(f"\nCollection ensured: {name!r}")

    info = client.get_collection(name)
    print("\n--- get_collection() -------------------------------------------------")
    print(info.model_dump_json(indent=2))

    vectors = info.config.params.vectors
    sparse = info.config.params.sparse_vectors
    print("\n--- checks --------------------------------------------------------------")

    ok = True

    def check(label: str, cond: bool) -> None:
        nonlocal ok
        print(f"  {'PASS' if cond else 'FAIL'}: {label}")
        ok = ok and cond

    check("named dense vector 'dense' exists", "dense" in vectors)
    if "dense" in vectors:
        check("dense size == 384", vectors["dense"].size == 384)
        check("dense distance == COSINE", str(vectors["dense"].distance) == "Distance.COSINE" or vectors["dense"].distance.value == "Cosine")

    check("named sparse vector 'sparse' exists", sparse is not None and "sparse" in sparse)

    schema = info.payload_schema
    indexed = set(schema.keys())

    if QDRANT_URL:
        # Real server: create_payload_index() actually builds indexes.
        check(f"payload indexes == exactly {set(INDEXED_PAYLOAD_FIELDS)}", indexed == set(INDEXED_PAYLOAD_FIELDS))
    else:
        # Embedded local mode: Qdrant's in-process backend does not build
        # payload indexes at all (create_payload_index() is a documented
        # no-op there — filtering still works correctly via full scan, this
        # is a performance-only limitation of local mode). payload_schema is
        # therefore expected to come back empty here.
        print("  NOTE: embedded local mode — Qdrant does not build payload indexes "
              "(create_payload_index() is a no-op there); payload_schema is expected "
              "empty. Filtering by tenant_id/project_id/stage_id still works via full "
              "scan. create_payload_index() calls are still made so this becomes a real "
              "index automatically if ever pointed at a server Qdrant.")
        check("payload_schema empty as expected in local mode", indexed == set())

    check("no 'sensitivity' field ever indexed/requested", not any("sensitiv" in f.lower() for f in INDEXED_PAYLOAD_FIELDS))
    check("no 'team' field ever indexed/requested", not any("team" in f.lower() for f in INDEXED_PAYLOAD_FIELDS))

    print("\n--- payload_schema (as reported by get_collection()) ------------------")
    if schema:
        for field, schema_info in schema.items():
            print(f"  {field}: {schema_info.data_type}")
    else:
        print("  (empty — see NOTE above)")

    print("\n--- functional filter check (real point, dense+sparse embeddings) -----")
    from fastembed import SparseTextEmbedding, TextEmbedding
    from qdrant_client import models as qm

    dense_model = TextEmbedding("BAAI/bge-small-en-v1.5")
    sparse_model = SparseTextEmbedding("Qdrant/bm25")

    text = "The contractor must submit test results before final sign-off."
    dense_vec = next(dense_model.embed([text])).tolist()
    sparse_vec = next(sparse_model.embed([text]))

    other_project = uuid.uuid4()
    other_stage = uuid.uuid4()
    target_project = uuid.uuid4()
    target_stage = uuid.uuid4()
    point_id = str(uuid.uuid4())

    client.upsert(
        collection_name=name,
        points=[
            qm.PointStruct(
                id=point_id,
                vector={
                    DENSE_VECTOR_NAME: dense_vec,
                    SPARSE_VECTOR_NAME: qm.SparseVector(
                        indices=sparse_vec.indices.tolist(),
                        values=sparse_vec.values.tolist(),
                    ),
                },
                payload={
                    "tenant_id": str(tenant.tenant_id),
                    "project_id": str(target_project),
                    "stage_id": str(target_stage),
                },
            )
        ],
    )

    def count_with_filter(project_id: uuid.UUID, stage_id: uuid.UUID) -> int:
        result = client.count(
            collection_name=name,
            count_filter=qm.Filter(
                must=[
                    qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=str(tenant.tenant_id))),
                    qm.FieldCondition(key="project_id", match=qm.MatchValue(value=str(project_id))),
                    qm.FieldCondition(key="stage_id", match=qm.MatchValue(value=str(stage_id))),
                ]
            ),
        )
        return result.count

    check("matching tenant/project/stage filter finds the point", count_with_filter(target_project, target_stage) == 1)
    check("wrong project_id filters it out", count_with_filter(other_project, target_stage) == 0)
    check("wrong stage_id filters it out", count_with_filter(target_project, other_stage) == 0)

    client.delete(collection_name=name, points_selector=qm.PointIdsList(points=[point_id]))
    check("cleanup: point removed", client.count(collection_name=name).count == 0)

    print("\n" + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"))
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
