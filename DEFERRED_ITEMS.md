# DocFlow AI — Deferred Items

Consolidated register of things that are **intentionally not built yet** —
distinct from things that were *rejected* (those live in `MERGE_DECISIONS.md`
under "Explicitly rejected"). Each entry says what's deferred, what stands in
for it today, and where the decision came from.

Numbering is stable — code comments reference these by number (e.g.
`app/services/rag/generation.py` cites **#15**). Add new items at the end;
don't renumber.

> Provenance: first assembled 2026-09-10 from `MERGE_DECISIONS.md`, in-code
> `Phase`/`TODO`/placeholder markers, and the RAG design discussion. Before
> this the list only existed informally.

---

## Foundation & infrastructure

**1. Local Google ID-token signature verification.**
OAuth sign-in verifies the Google ID token via Google's `tokeninfo` endpoint
(a network round-trip per login) rather than verifying the JWT signature
locally against Google's public keys. Acceptable for now.
_Source: MERGE_DECISIONS §2._

**2. Object storage for document bytes.**
Document file bytes are stored in Postgres (`document_versions.file_data`,
`LargeBinary`). `Document.logical_path()` already pre-computes an
S3/MinIO-style path so a future migration has no path-design ambiguity, but
the migration itself is not done.
_Source: Phase 0; `app/models/document.py`._

**3. Containerisation / deployment.**
No Docker or deployment tooling on either side of the merge.
_Source: MERGE_DECISIONS §7._

## Documents & scanning

**4. "Upload a new version from a new file" action.**
New `document_versions` rows are produced only by the post-upload
review-chat finalize (`finalize_document_revision`). Uploading a replacement
file into an existing document's context as a new version is not built.
_Source: `app/models/document.py` (`DocumentVersion` docstring)._

**5. Async Structure Scanner worker.**
`app/workers/structure_scanner_worker.py` is still the Phase-1 placeholder.
Scoring + reform + injection scan now run **inline** at finalize
(`run_full_scan`), off no queue. Revisit only if scanning needs to move off
the request path.
_Source: `app/workers/structure_scanner_worker.py`._

## Approval workflow

**6. Document-type-based approval triggers.**
Approval is gated per-stage only (`Stage.requires_approval`). "This document
*type* always needs sign-off regardless of stage" (e.g. contracts) is not
modelled.
_Source: MERGE_DECISIONS §3/4._

**7. Notification delivery for access-request / approval events.**
`record_audit` writes an audit row; nothing is actually delivered to the
approver (no email, no in-app inbox). Approvers currently have to look at the
`/access-requests/pending` and pending-approvals endpoints.
_Source: MERGE_DECISIONS §1 ("email notifications" listed to adopt)._

**8. "Cards" UI for pending access-requests / approvals.**
Surfaced via API + chat agent tools only. The card-style frontend treatment
waits on real frontend work.
_Source: MERGE_DECISIONS §3/4._

## RAG — retrieval & indexing

**9. Qdrant-side ABAC via chunk-payload filters.**
ABAC stays entirely in the app layer: retrieval resolves the allowed
stage_ids / document_ids in Postgres and passes those down; the vector store
payload indexes only `tenant_id` / `project_id` / `stage_id`. Pushing
sensitivity / team filters into Qdrant (his `ChunkPayload` idea) is deferred
to avoid two ABAC implementations drifting apart.
_Source: MERGE_DECISIONS §4; `app/services/rag/collection_setup.py`._

**10. Transitive stage-reference chains in retrieval scope.**
Retrieval scope expands exactly one hop (a stage plus the stages it directly
references). Following chains (A → B → C) is deliberately not done.
_Source: `app/services/rag/stage_scope.py`._

**11. Retrieval tuning knobs — a proper labelled eval set.**
`COARSE_LIMIT`, `TOP_K`, `RELEVANCE_FLOOR` are fixed global constants — no
per-project or per-query tuning, and calibrated by hand against a small
synthetic set (`scripts/verify_relevance_floor.py`), not a real labelled
retrieval eval. (`RELEVANCE_FLOOR` itself was a live bug — miscalibrated at
`0.0` — and is now fixed at `-9.0`; this item is the remaining work: a real
eval set and, eventually, tuning per corpus.)
_Source: `app/services/rag/retrieval.py`, `reranking.py`._

**12. Conversational query rewriting / expansion.**
`search_documents` retrieves on the raw user question each turn. Prior turns
are used by the agent for tool-routing and phrasing, but not to rewrite or
expand the retrieval query itself.
_Source: `app/tools/rag_tools.py`._

## RAG — agent UX

**13. Follow-up-question suggestions after an answer.**
Listed as an adoptable-later UX pattern; not built.
_Source: MERGE_DECISIONS §5._

**14. Clickable citation deep-links.**
Citations are text labels (`[Source 2 — Requirements stage]`). No link back
to the specific document / section in a UI.

**15. Post-generation groundedness / self-check pass for `search_documents`.**
After `generate_answer()` returns a cited answer, there is **no** second pass
(LLM or heuristic) that re-checks each sentence against its cited chunk and
drops or flags unsupported claims. Deliberately skipped for now. The
safeguards in place instead:
- the citation-constrained system prompt (every claim must cite a provided
  source label; invented labels forbidden),
- retrieved chunk text framed as untrusted data,
- generation temperature 0.1,
- a fixed "The retrieved documents don't contain an answer to that." fallback
  when the chunks don't support an answer,
- generation runs with **no tools bound**, so the answer can't trigger an
  action.
_Source: `app/services/rag/generation.py`; MERGE_DECISIONS §5 lists
"answer-groundedness self-check" as an adoptable-later pattern._

## Other agents

**16. General Query Agent.**
Metadata / gap-context Q&A, kept as a separate agent from the RAG content
agent per the original design. Parked; `/query` in the CLI reports that it
isn't wired up.
_Source: MERGE_DECISIONS §5._

**17. Gap Detection / Coverage Agent.**
`required_documents`-driven, per-project, `is_mandatory`-weighted completion
percentage, with his Markdown gap-report formatting (checkboxes + %) as the
presentation pattern. Designed, not built.
_Source: MERGE_DECISIONS §5._
