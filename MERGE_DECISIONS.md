# DocFlow AI — Repo Merge Decisions

Tracking document for reconciling two independently-built implementations:
- **Mine** (`meemansha004/DocFlow-AI`) — Postgres/SQLAlchemy, Groq/Agno agent layer, CLI-only, ABAC built this week
- **His** (`apurvharsh/docflow-app`) — SQLite, Gemini + Qdrant (Vector Indexing already built), Google OAuth, full React UI

Goal: best-of-both architecture, not a straight pick between the two. Final result lives in `meemansha004/DocFlow-AI`.

---

## Section 1: Foundation

| Item | Decision |
|---|---|
| ORM | **Keep mine** — SQLAlchemy + Alembic (his raw sqlite3 + runtime ALTER TABLE checks rejected — no migration history/rollback) |
| Sensitivity type | **Adopt `IntEnum`** (`public=0, internal=1, confidential=2`) — combines his integer-comparison simplicity with readable code; no separate rank dict needed |
| Team/role granularity | **Keep mine** — role varies per team (`viewer/contributor/team_lead`), not per project. His model (role per project only) can't represent a user having different roles on different teams (e.g. erin: viewer on Design, team_lead on Engineering) |
| Document-team visibility | **Keep mine** — proper join table `document_team_visibility` (his denormalized JSON array on the document row rejected — harder to query) |
| New features to adopt from his side | `audit_log`, `workflow_state` (see Section 3/4 below), password reset flow, email notifications, `chat_sessions`/`chat_messages`. **Reject**: `personal_documents` (not part of final scope) |
| Stages | **Keep mine** — per-project customizable stages (order_index, soft-delete) + `required_documents` checklist. His global fixed 14-stage SDLC taxonomy per tenant rejected (no per-project customization, no checklist concept) |

## Section 2: Authentication & Identity

- His implementation is genuinely complete and adoptable: PBKDF2-HMAC-SHA256 password hashing (310k iterations), HMAC-signed session tokens, full Google OAuth2 flow (CSRF-protected state, code exchange, ID token verification).
- His `UserContext` dataclass concept is good (a resolved-identity object auth produces) — but needs rebuilding so `role`/`team_memberships` reflect **per-team** roles (matching Section 1's C decision), not per-project.
- Minor note (not a blocker): his Google ID token verification uses the `tokeninfo` endpoint (network round-trip) rather than local JWT signature verification — acceptable for now, worth revisiting later.

## Section 3: ABAC/RBAC enforcement logic

**Adding (new, adopted from his side):**
1. ~~"Uploader can always view their own document" — REMOVED per later decision. `can_view_document()` stays exactly as originally built, no special-case bypass for the uploader.~~
2. Reference for future action-gating: `submit`/`approve`/`reject` actions (tied to `workflow_state`) should require `team_lead`+ — consistent with existing action-gating decisions.

**Staying as-is:** `has_permission()`, `can_view_document()`, `build_access_filter()` — all existing logic unchanged aside from addition #1 above.

**Explicitly rejected:**
1. His per-project (not per-team) role model.
2. The `REVIEWER` auto-upgraded-to-`ADMIN` behavior in his `role_for_project()` — looks like an unintentional bug, not a design choice worth replicating.
3. His role-naming scheme (`member/reviewer/admin/team_lead`) — keep our own naming (`viewer/contributor/team_lead` + separate `project_admin`/`org_admin`).

## Section 3/4 crossover: Document approval workflow (`workflow_state`)

Adopting his `workflow_state` concept (draft → pending_review → approved/rejected), but scoped deliberately (not blanket-mandatory, to avoid becoming a team_lead bottleneck):

- **Trigger granularity:** Stage-gate only for now — new `Stage.requires_approval: bool` column. Document-type-based approval (e.g. "Contracts always need sign-off regardless of stage") deferred to future scope.
- **Submission:** **Manual** — user explicitly submits for review after a clean Scanner pass (matches the existing `confirm_upload` "explicit confirmation" pattern already built). Automatic submission rejected as more error-prone (removes the person's ability to say "not yet ready" even after checks pass — same reasoning as why GitHub PRs require an explicit "Request Review" rather than auto-requesting on green CI).
- **New table:** `workflow_state` (rebuilt as SQLAlchemy model) — `document_id`, `state`, `approved_by`, `approval_timestamp`, `rejection_reason`.
- **New actions:** `submit`, `approve`, `reject` — gated via `has_permission()`; `approve`/`reject` require `team_lead`+ on that document's specific team.
- **Notification:** two parallel systems, kept deliberately separate — access-authorization requests (confidential-clearance grants) and document-approval requests are NOT merged into one system. Both surfaced via chat Query-agent tools for now (`list_pending_access_requests`, `list_pending_approvals`); "cards" UI treatment deferred until real frontend work happens.

## Section 4 (continued): Drafting agent — decoupled from persistence (design pivot)

**Real architectural decision, prompted by reviewing his `document_workflow.py`:**

- **Drafting no longer requires `doc_type` or `stage`.** The `extract_doc_type_and_stage` gate is removed entirely from Drafting Agent's flow — user describes what they want in free-form language, agent drafts directly, no blocking questions about type/stage.
- **Scanner still runs unchanged** — structural quality check stays exactly as built.
- **Chat's responsibility ends at presenting the finished, scanned document** — shown in chat, downloadable. That's it.
- **Actual persistence (tying to a real stage/team/project, `has_permission()`/sensitivity checks, DB write) happens through a UI "Upload" button/feature, NOT through chat.** This is a genuine design decision (chat drafts, UI persists), not a scope cut — both are real, planned parts of the system, just different entry points.
- **Impact on existing code:**
  - `extract_doc_type_and_stage` tool — retired from Drafting Agent's toolset
  - `confirm_upload` tool (chat version) — retired; its logic (`create_document()`) moves to a future API endpoint triggered by the UI, not an agent tool call
  - `create_document()`, `has_permission()`, `resolve_sensitivity()` — all stay exactly as built, correct logic, just triggered from a different entry point going forward
  - `draft_context` in `cli.py` simplifies significantly — no more doc_type/stage/draft_confirmed/upload-tracking fields needed for the chat flow

**Also noted from reviewing his `document_workflow.py` (not yet decided how/whether to adopt):**
- His draft generation calls Gemini with a fallback to a hardcoded template generator if the LLM call fails. **Decision: reject the hardcoded-template fallback** — on LLM failure, raise a clean, honest error instead (e.g. "Unable to draft right now — please try again in a moment"), consistent with the "never fabricate" principle held throughout this project.
- His `score_document` is pure heuristic/regex-based (no LLM call) — faster/cheaper but semantically shallow (can't detect e.g. a "Test Cases" section that just says "see spreadsheet"). **Our LLM-judged rubric stays** — clearly the more sophisticated approach.
- His `reform_document` is a generic hardcoded template with original text appended, not a true content-preserving reformation. **Our content-locked LLM reformation stays** — clearly better.
- Alias/keyword-matching fast path for extraction (his `DOCUMENT_TYPE_ALIASES`/`STAGE_ALIASES` dicts) — **moot now**, since stage/doc_type extraction is being removed from the drafting flow entirely per the pivot above.

## Section 4: Document handling (sensitivity/RAG bridge, flagged for later)

- His `ChunkPayload` dataclass (Qdrant chunk metadata: `document_id, project_id, stage, doc_type, section_title, visible_to_teams, sensitivity_level, workflow_state, chunk_text, chunk_index`) is a strong candidate for how Qdrant-side ABAC enforcement should work once Vector Indexing is actually designed — **revisit once we design the RAG architecture together, not decided yet.**
- 3-tier sensitivity confirmed (his originally had 4 including `restricted` — dropped to match our earlier collapse decision).

*(Remainder of Section 4 — upload flow storage approach, Structure Scanner equivalent, versioning — still to be compared)*

---

## Section 5: Agent/AI layer

- **Command routing convergence:** his CLI independently uses the same `/draft /scan /rag /query` pattern — validates our approach. His internal trigger detection uses fragile substring matching (`"confirm" in text.lower()`) — confirms our tool-call-verification approach is more robust; nothing adopted from his implementation here.
- **Gap Detection Agent:** his version is a hardcoded, global, non-customizable `STAGE_REQUIREMENTS` dict with substring matching — no LLM, no DB. **Our planned design (DB-backed `required_documents` table, per-project customizable, `is_mandatory`-driven coverage %) stays as the target.** Adoptable: his Markdown gap-report formatting (checkboxes + completion %) as a presentation pattern.
- **Scanner Agent:** confirmed his numeric score is ALWAYS computed by a deterministic heuristic function (headings/word-count/keyword-presence), even though it's wrapped in a real Agno+Groq agent — the LLM only narrates, never judges. **Our LLM-judged rubric stays — more sophisticated, catches semantic problems his heuristic can't.** Adoptable idea (not exact implementation): graceful fallback to a baseline response if the LLM call fails, rather than a hard crash.
- **General Query Agent + RAG:** PARKED — not decided yet, to be revisited as a dedicated last discussion. Noted for later: his agent merges content-Q&A and metadata/gap-context into one, vs. our original design keeping RAG Agent and Query Agent separate — real fork to resolve later. Adoptable UX patterns regardless: forced citation format, answer-groundedness self-check, follow-up-question suggestions.

## Section 6: Frontend/UI

**Adopt his React frontend as the starting foundation** — you currently have zero UI. Real routing, protected routes, token-based auth (Bearer token in localStorage), reusable component library, pages for every major feature. His API client already anticipates a `pendingApprovals(projectId)` call — lines up directly with the `workflow_state` feature being adopted. Integration work needed: update his API contract calls to match our finalized backend (per-team roles, actual `has_permission()` semantics) — not a UI redesign, just rewiring.

## Section 7: Infrastructure/DevOps

- No Docker/containerization on either side.
- No actual test files on his side (pytest listed as a dependency, but unused).
- Adoptable: `/auth/demo` (one-click admin token for fast demos), `/rbac/matrix` + `/abac/simulate` admin endpoints (a concrete way to visualize/demo the permission system working), and his README's feature-to-file mapping table as a documentation pattern.

## Explicitly parked for a later, dedicated discussion

- **RAG / Vector Indexing architecture** (Qdrant collection design, `ChunkPayload`, hybrid search, reranker, `build_access_filter()` for vector search)
- **General Query Agent** (since its design depends directly on the RAG architecture decision above)

---

## Implementation (starting now)

Comparison/decision phase complete for Sections 1-4, 6, 7 and the non-RAG parts of Section 5. Implementation begins now, in the order to be determined next. RAG and General Query Agent remain excluded from this implementation pass.