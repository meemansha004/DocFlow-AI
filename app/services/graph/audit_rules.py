import uuid
from typing import Any, Dict, List, Optional, Set, Tuple
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.graph import Edge, Node
from app.models.stage import Stage, StageReference, TeamStageAccess
from app.models.document import Document, DocumentVersion, DocumentStatus
from app.models.required_document import RequiredDocument
from app.models.workflow import WorkflowState, WorkflowStatus
from app.models.team import Team


class FindingSpec:
    def __init__(
        self,
        rule_code: str,
        severity: str,
        is_blocker: bool,
        title: str,
        description: str,
        affected_entity_type: str,
        affected_entity_id: uuid.UUID,
        target_stage_id: Optional[uuid.UUID] = None,
        evidence_sources: Optional[List[Any]] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        self.rule_code = rule_code
        self.severity = severity
        self.is_blocker = is_blocker
        self.title = title
        self.description = description
        self.affected_entity_type = affected_entity_type
        self.affected_entity_id = affected_entity_id
        self.target_stage_id = target_stage_id
        self.evidence_sources = evidence_sources or []
        self.details = details or {}


def evaluate_r001_missing_mandatory_requirements(
    db: Session,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    evaluated_stage_ids: List[uuid.UUID],
    stage_name_map: Dict[uuid.UUID, str],
) -> List[FindingSpec]:
    """
    R001: Missing Mandatory Requirement Evidence.
    Evaluates requirements applicable to evaluated stages.
    Considers both originating stage and explicit APPLIES_TO edges in the graph.
    Prevents downstream contamination (a requirement originating downstream cannot contaminate upstream).
    Flags when an applicable mandatory requirement has zero approved satisfying/evidentiary documents.
    """
    findings: List[FindingSpec] = []
    if not evaluated_stage_ids:
        return findings

    # Active stages to determine topological ceiling (no downstream contamination)
    stages = (
        db.query(Stage)
        .filter(Stage.project_id == project_id, Stage.deleted_at.is_(None))
        .order_by(Stage.order_index.asc())
        .all()
    )
    stage_order = {s.stage_id: s.order_index for s in stages}
    max_evaluated_order = max([stage_order.get(sid, -1) for sid in evaluated_stage_ids], default=-1)

    # 1. Requirements originating in evaluated stages
    originating_reqs = (
        db.query(RequiredDocument)
        .filter(
            RequiredDocument.stage_id.in_(evaluated_stage_ids),
            RequiredDocument.is_mandatory.is_(True),
        )
        .all()
    )

    # 2. Cross-stage requirements: Requirements that have an explicit APPLIES_TO edge pointing to an evaluated stage
    eval_stage_nodes = (
        db.query(Node)
        .filter(
            Node.tenant_id == tenant_id,
            Node.project_id == project_id,
            Node.source_table == "stages",
            Node.source_id.in_(evaluated_stage_ids),
        )
        .all()
    )
    eval_stage_node_map = {n.node_id: n.source_id for n in eval_stage_nodes}

    applies_to_edges = (
        db.query(Edge)
        .filter(
            Edge.tenant_id == tenant_id,
            Edge.project_id == project_id,
            Edge.target_node_id.in_(list(eval_stage_node_map.keys())),
            Edge.edge_type == "APPLIES_TO",
        )
        .all()
    ) if eval_stage_node_map else []

    req_node_to_stage_ids: Dict[uuid.UUID, Set[uuid.UUID]] = {}
    for e in applies_to_edges:
        st_id = eval_stage_node_map.get(e.target_node_id)
        if st_id:
            req_node_to_stage_ids.setdefault(e.source_node_id, set()).add(st_id)

    cross_req_nodes = (
        db.query(Node)
        .filter(
            Node.tenant_id == tenant_id,
            Node.project_id == project_id,
            Node.source_table == "required_documents",
            Node.node_id.in_(list(req_node_to_stage_ids.keys())),
        )
        .all()
    ) if req_node_to_stage_ids else []

    node_to_req_id = {n.node_id: n.source_id for n in cross_req_nodes}
    req_target_stages: Dict[uuid.UUID, Set[uuid.UUID]] = {}
    for node_id, stage_ids in req_node_to_stage_ids.items():
        if node_id in node_to_req_id:
            req_target_stages.setdefault(node_to_req_id[node_id], set()).update(stage_ids)

    cross_reqs = (
        db.query(RequiredDocument)
        .filter(
            RequiredDocument.requirement_id.in_(list(req_target_stages.keys())),
            RequiredDocument.is_mandatory.is_(True),
        )
        .all()
    ) if req_target_stages else []

    # Combine unique requirements with target evaluated stages
    all_candidate_reqs: Dict[uuid.UUID, Tuple[RequiredDocument, Set[uuid.UUID]]] = {}
    for req in originating_reqs:
        all_candidate_reqs.setdefault(req.requirement_id, (req, set()))[1].add(req.stage_id)

    for req in cross_reqs:
        orig_order = stage_order.get(req.stage_id, 999999)
        if orig_order <= max_evaluated_order:
            entry = all_candidate_reqs.setdefault(req.requirement_id, (req, set()))
            entry[1].update(req_target_stages.get(req.requirement_id, set()))

    for req_id, (req, target_stages) in all_candidate_reqs.items():
        target_stages_in_scope = target_stages.intersection(set(evaluated_stage_ids))
        if not target_stages_in_scope:
            continue

        req_node = (
            db.query(Node)
            .filter(
                Node.tenant_id == tenant_id,
                Node.project_id == project_id,
                Node.source_table == "required_documents",
                Node.source_id == req.requirement_id,
            )
            .first()
        )
        if not req_node:
            continue

        evidence_edges = (
            db.query(Edge)
            .filter(
                Edge.tenant_id == tenant_id,
                Edge.project_id == project_id,
                Edge.target_node_id == req_node.node_id,
                Edge.edge_type.in_(["ESTABLISHES", "IMPLEMENTS", "VALIDATES", "EVIDENCES"]),
            )
            .all()
        )

        has_approved_evidence = False
        evidence_doc_titles = []

        for edge in evidence_edges:
            doc_node = db.query(Node).filter(Node.node_id == edge.source_node_id).first()
            if doc_node and doc_node.source_table == "documents":
                doc = db.query(Document).filter(Document.document_id == doc_node.source_id).first()
                if doc:
                    evidence_doc_titles.append(doc.original_filename)
                    wf = (
                        db.query(WorkflowState)
                        .filter(WorkflowState.document_id == doc.document_id)
                        .first()
                    )
                    stage = db.query(Stage).filter(Stage.stage_id == doc.stage_id).first()
                    current_wf_state = wf.state.value if (wf and hasattr(wf.state, "value")) else (wf.state if wf else "draft")
                    if stage and not stage.requires_approval:
                        has_approved_evidence = True
                        break
                    elif current_wf_state == "approved":
                        has_approved_evidence = True
                        break

        if not has_approved_evidence:
            for t_stage_id in target_stages_in_scope:
                stage_name = stage_name_map.get(t_stage_id, "Unknown Stage")
                findings.append(
                    FindingSpec(
                        rule_code="R001",
                        severity="CRITICAL",
                        is_blocker=True,
                        title="Missing Mandatory Requirement Evidence",
                        description=f"Mandatory requirement '{req.name}' applicable to stage '{stage_name}' has no approved evidence documents.",
                        affected_entity_type="requirement",
                        affected_entity_id=req.requirement_id,
                        target_stage_id=t_stage_id,
                        details={
                            "stage_name": stage_name,
                            "entity_label": req.name,
                            "originating_stage_id": str(req.stage_id),
                            "is_mandatory": True,
                            "existing_unapproved_evidence": evidence_doc_titles,
                        },
                    )
                )

    return findings


def evaluate_r002_unapproved_documents_in_gate_stages(
    db: Session,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    evaluated_stage_ids: List[uuid.UUID],
    stage_name_map: Dict[uuid.UUID, str],
) -> List[FindingSpec]:
    """
    R002: Unapproved Document in Gate Stage.
    For stages where requires_approval = True, documents must have state 'approved'.
    Specifically flags unapproved documents in 'draft' or 'rejected' state.
    (Documents in 'pending_review' are actively awaiting review and are flagged by R010).
    """
    findings: List[FindingSpec] = []
    gate_stages = (
        db.query(Stage)
        .filter(
            Stage.stage_id.in_(evaluated_stage_ids),
            Stage.requires_approval.is_(True),
            Stage.deleted_at.is_(None),
        )
        .all()
    )

    for stage in gate_stages:
        docs = (
            db.query(Document)
            .filter(Document.project_id == project_id, Document.stage_id == stage.stage_id)
            .all()
        )
        for doc in docs:
            wf = (
                db.query(WorkflowState)
                .filter(WorkflowState.document_id == doc.document_id)
                .first()
            )
            current_state = wf.state.value if (wf and hasattr(wf.state, "value")) else (wf.state if wf else "draft")
            if current_state != "approved":
                stage_name = stage_name_map.get(stage.stage_id, stage.name)
                findings.append(
                    FindingSpec(
                        rule_code="R002",
                        severity="HIGH",
                        is_blocker=True,
                        title="Unapproved Document in Gate Stage",
                        description=f"Document '{doc.original_filename}' in approval-required stage '{stage_name}' has status '{current_state}' instead of 'approved'.",
                        affected_entity_type="document",
                        affected_entity_id=doc.document_id,
                        target_stage_id=stage.stage_id,
                        details={
                            "stage_name": stage_name,
                            "entity_label": doc.original_filename,
                            "current_state": str(current_state),
                        },
                    )
                )

    return findings



def evaluate_r003_broken_stage_dependencies(
    db: Session,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    evaluated_stage_ids: List[uuid.UUID],
    stage_name_map: Dict[uuid.UUID, str],
) -> List[FindingSpec]:
    """
    R003: Broken Stage Dependency.
    Respects directional traversal. When a document in stage S has a functional dependency
    (DEPENDS_ON) on an upstream document U, but U is unapproved or in rejected state.
    (Strictly distinct from lifecycle sequencing PRECEDES).
    """
    findings: List[FindingSpec] = []
    # Find all DEPENDS_ON edges where source document is in evaluated stages
    docs_in_scope = (
        db.query(Document)
        .filter(Document.project_id == project_id, Document.stage_id.in_(evaluated_stage_ids))
        .all()
    )
    doc_ids_in_scope = {d.document_id: d for d in docs_in_scope}

    if not doc_ids_in_scope:
        return findings

    doc_nodes = (
        db.query(Node)
        .filter(
            Node.project_id == project_id,
            Node.source_table == "documents",
            Node.source_id.in_(list(doc_ids_in_scope.keys())),
        )
        .all()
    )
    node_to_doc = {n.node_id: doc_ids_in_scope[n.source_id] for n in doc_nodes}

    if not node_to_doc:
        return findings

    dep_edges = (
        db.query(Edge)
        .filter(
            Edge.source_node_id.in_(list(node_to_doc.keys())),
            Edge.edge_type == "DEPENDS_ON",
        )
        .all()
    )

    for edge in dep_edges:
        target_node = db.query(Node).filter(Node.node_id == edge.target_node_id).first()
        if target_node and target_node.source_table == "documents":
            upstream_doc = db.query(Document).filter(Document.document_id == target_node.source_id).first()
            if upstream_doc:
                # Check upstream document workflow approval
                wf = (
                    db.query(WorkflowState)
                    .filter(WorkflowState.document_id == upstream_doc.document_id)
                    .first()
                )
                upstream_stage = db.query(Stage).filter(Stage.stage_id == upstream_doc.stage_id).first()
                requires_appr = upstream_stage.requires_approval if upstream_stage else False
                state = wf.state.value if (wf and hasattr(wf.state, "value")) else (wf.state if wf else "draft")

                if (requires_appr and state != "approved") or state == "rejected":
                    consumer_doc = node_to_doc[edge.source_node_id]
                    stage_name = stage_name_map.get(consumer_doc.stage_id, "Unknown Stage")
                    findings.append(
                        FindingSpec(
                            rule_code="R003",
                            severity="HIGH",
                            is_blocker=True,
                            title="Broken Upstream Dependency",
                            description=f"Document '{consumer_doc.original_filename}' depends on upstream document '{upstream_doc.original_filename}', which is not approved (status: '{state}').",
                            affected_entity_type="document",
                            affected_entity_id=consumer_doc.document_id,
                            target_stage_id=consumer_doc.stage_id,
                            details={
                                "stage_name": stage_name,
                                "entity_label": consumer_doc.original_filename,
                                "upstream_document": upstream_doc.original_filename,
                                "upstream_status": state,
                            },
                        )
                    )

    return findings


def evaluate_r004_stale_document_references(
    db: Session,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    evaluated_stage_ids: List[uuid.UUID],
    stage_name_map: Dict[uuid.UUID, str],
) -> List[FindingSpec]:
    """
    R004: Stale Document Reference.
    Detects when a document in an evaluated stage references an older version of another document,
    and a newer finalized/approved version of that referenced document exists.

    The finding identifies:
    - referencing document
    - referenced document
    - referenced version
    - newer available version
    - relevant stage/context
    - why the reference is stale
    """
    findings: List[FindingSpec] = []
    docs_in_scope = (
        db.query(Document)
        .filter(Document.project_id == project_id, Document.stage_id.in_(evaluated_stage_ids))
        .all()
    )
    doc_map = {d.document_id: d for d in docs_in_scope}
    if not doc_map:
        return findings

    doc_nodes = (
        db.query(Node)
        .filter(
            Node.project_id == project_id,
            Node.source_table == "documents",
            Node.source_id.in_(list(doc_map.keys())),
        )
        .all()
    )
    node_to_doc = {n.node_id: doc_map[n.source_id] for n in doc_nodes}
    if not node_to_doc:
        return findings

    # Check REFERENCES and DEPENDS_ON edges originating from these documents
    ref_edges = (
        db.query(Edge)
        .filter(
            Edge.source_node_id.in_(list(node_to_doc.keys())),
            Edge.edge_type.in_(["REFERENCES", "DEPENDS_ON"]),
        )
        .all()
    )

    for edge in ref_edges:
        target_node = db.query(Node).filter(Node.node_id == edge.target_node_id).first()
        if not target_node:
            continue

        target_doc = None
        referenced_version_number: Optional[int] = None

        if target_node.source_table == "documents":
            target_doc = db.query(Document).filter(Document.document_id == target_node.source_id).first()
            props = edge.properties or {}
            if "referenced_version_number" in props:
                referenced_version_number = int(props["referenced_version_number"])
            elif "referenced_version_id" in props:
                ref_ver = db.query(DocumentVersion).filter(DocumentVersion.version_id == uuid.UUID(str(props["referenced_version_id"]))).first()
                if ref_ver:
                    referenced_version_number = ref_ver.version_number
        elif target_node.source_table == "document_versions":
            ref_ver = db.query(DocumentVersion).filter(DocumentVersion.version_id == target_node.source_id).first()
            if ref_ver:
                referenced_version_number = ref_ver.version_number
                target_doc = db.query(Document).filter(Document.document_id == ref_ver.document_id).first()

        if not target_doc or referenced_version_number is None:
            continue

        # Check if newer finalized/approved versions of target_doc exist
        # Finalized status is DocumentStatus.indexed
        newer_versions = (
            db.query(DocumentVersion)
            .filter(
                DocumentVersion.document_id == target_doc.document_id,
                DocumentVersion.version_number > referenced_version_number,
                DocumentVersion.status == DocumentStatus.indexed,
            )
            .order_by(DocumentVersion.version_number.desc())
            .all()
        )

        if newer_versions:
            latest_newer = newer_versions[0]
            consumer_doc = node_to_doc[edge.source_node_id]
            stage_name = stage_name_map.get(consumer_doc.stage_id, "Unknown Stage")
            findings.append(
                FindingSpec(
                    rule_code="R004",
                    severity="MEDIUM",
                    is_blocker=False,
                    title="Stale Document Reference",
                    description=(
                        f"Document '{consumer_doc.original_filename}' in stage '{stage_name}' references "
                        f"version {referenced_version_number} of '{target_doc.original_filename}', but a newer "
                        f"finalized version (v{latest_newer.version_number}) is available."
                    ),
                    affected_entity_type="document",
                    affected_entity_id=consumer_doc.document_id,
                    target_stage_id=consumer_doc.stage_id,
                    details={
                        "stage_name": stage_name,
                        "referencing_document": consumer_doc.original_filename,
                        "referenced_document": target_doc.original_filename,
                        "referenced_version": referenced_version_number,
                        "newer_available_version": latest_newer.version_number,
                        "reason": f"Target document has been updated to finalized version {latest_newer.version_number}.",
                    },
                )
            )

    return findings


def evaluate_r005_dependency_cycles(

    db: Session,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    evaluated_stage_ids: List[uuid.UUID],
    stage_name_map: Dict[uuid.UUID, str],
) -> List[FindingSpec]:
    """
    R005: Dependency Cycle Detection across documents using Tarjan / DFS.
    """
    findings: List[FindingSpec] = []
    dep_edges = (
        db.query(Edge)
        .filter(Edge.project_id == project_id, Edge.edge_type == "DEPENDS_ON")
        .all()
    )
    if not dep_edges:
        return findings

    # Build adjacency graph
    adj: Dict[uuid.UUID, List[uuid.UUID]] = {}
    for edge in dep_edges:
        adj.setdefault(edge.source_node_id, []).append(edge.target_node_id)

    visited: Set[uuid.UUID] = set()
    rec_stack: Set[uuid.UUID] = set()
    cycle_nodes: Set[uuid.UUID] = set()

    def dfs(node_id: uuid.UUID):
        visited.add(node_id)
        rec_stack.add(node_id)

        for neighbor in adj.get(node_id, []):
            if neighbor not in visited:
                dfs(neighbor)
            elif neighbor in rec_stack:
                cycle_nodes.add(node_id)
                cycle_nodes.add(neighbor)

        rec_stack.remove(node_id)

    for node in list(adj.keys()):
        if node not in visited:
            dfs(node)

    for c_node_id in cycle_nodes:
        node = db.query(Node).filter(Node.node_id == c_node_id).first()
        if node and node.source_table == "documents":
            doc = db.query(Document).filter(Document.document_id == node.source_id).first()
            if doc and doc.stage_id in evaluated_stage_ids:
                stage_name = stage_name_map.get(doc.stage_id, "Unknown Stage")
                findings.append(
                    FindingSpec(
                        rule_code="R005",
                        severity="CRITICAL",
                        is_blocker=True,
                        title="Cyclic Document Dependency",
                        description=f"Document '{doc.original_filename}' is part of a circular DEPENDS_ON cycle.",
                        affected_entity_type="document",
                        affected_entity_id=doc.document_id,
                        target_stage_id=doc.stage_id,
                        details={
                            "stage_name": stage_name,
                            "entity_label": doc.original_filename,
                        },
                    )
                )

    return findings


def evaluate_r006_orphan_entities(
    db: Session,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    evaluated_stage_ids: List[uuid.UUID],
    stage_name_map: Dict[uuid.UUID, str],
) -> List[FindingSpec]:
    """
    R006: True Orphan Entity.
    Detects documents that lack a valid/required relationship for normal project operation:
    - no valid active project association
    - no valid active stage assignment (missing or unmapped to project's active stages)
    - no owner team where ownership is required (uploaded_as_team_id missing or not in project)

    Does NOT classify legitimate tenant-level entities (e.g. user nodes) as orphans.
    Distinguishes intentionally archived/deleted stages (where historical references are allowed).
    """
    findings: List[FindingSpec] = []

    # Valid project teams
    project_teams = db.query(Team).filter(Team.project_id == project_id).all()
    project_team_ids = {t.team_id for t in project_teams}

    # Valid active project stages
    active_stages = (
        db.query(Stage)
        .filter(Stage.project_id == project_id, Stage.deleted_at.is_(None))
        .all()
    )
    active_stage_ids = {s.stage_id for s in active_stages}

    # Documents associated with this project or claimed to belong to evaluated stages
    docs = db.query(Document).filter(Document.project_id == project_id).all()

    for doc in docs:
        doc_stage_id = getattr(doc, "stage_id", None)

        # 1. Missing or invalid stage
        if doc_stage_id is None or doc_stage_id not in active_stage_ids:
            # Check if stage was intentionally soft-deleted (historical reference preserved)
            is_soft_deleted_stage = False
            if doc_stage_id is not None:
                archived_stage = db.query(Stage).filter(Stage.stage_id == doc_stage_id).first()
                if archived_stage and archived_stage.deleted_at is not None:
                    is_soft_deleted_stage = True

            if not is_soft_deleted_stage:
                findings.append(
                    FindingSpec(
                        rule_code="R006",
                        severity="HIGH",
                        is_blocker=True,
                        title="Orphan Document: Missing Stage Assignment",
                        description=f"Document '{doc.original_filename}' has no valid active stage assignment in project.",
                        affected_entity_type="document",
                        affected_entity_id=doc.document_id,
                        target_stage_id=None,
                        details={
                            "entity_label": doc.original_filename,
                            "orphan_reason": "missing_or_invalid_stage",
                            "stage_id": str(doc_stage_id) if doc_stage_id else None,
                        },
                    )
                )
                continue

        # If stage is valid, only evaluate if in evaluated stages
        if doc_stage_id not in evaluated_stage_ids:
            continue

        stage_name = stage_name_map.get(doc_stage_id, "Unknown Stage")

        # 2. Missing owner team where ownership is required
        owner_team_id = getattr(doc, "uploaded_as_team_id", None)
        if owner_team_id is None or owner_team_id not in project_team_ids:
            findings.append(
                FindingSpec(
                    rule_code="R006",
                    severity="HIGH",
                    is_blocker=True,
                    title="Orphan Document: Missing Owner Team",
                    description=f"Document '{doc.original_filename}' in stage '{stage_name}' has no valid owner team assigned in this project.",
                    affected_entity_type="document",
                    affected_entity_id=doc.document_id,
                    target_stage_id=doc_stage_id,
                    details={
                        "stage_name": stage_name,
                        "entity_label": doc.original_filename,
                        "orphan_reason": "missing_owner_team",
                        "owner_team_id": str(owner_team_id) if owner_team_id else None,
                    },
                )
            )

        # 3. Invalid project association
        if doc.project_id is None or doc.project_id != project_id:
            findings.append(
                FindingSpec(
                    rule_code="R006",
                    severity="HIGH",
                    is_blocker=True,
                    title="Orphan Document: Invalid Project Association",
                    description=f"Document '{doc.original_filename}' has an invalid project association.",
                    affected_entity_type="document",
                    affected_entity_id=doc.document_id,
                    target_stage_id=doc_stage_id,
                    details={
                        "entity_label": doc.original_filename,
                        "orphan_reason": "invalid_project_association",
                        "project_id": str(doc.project_id) if doc.project_id else None,
                    },
                )
            )

    return findings


def evaluate_r007_permitted_stage_reference_violations(

    db: Session,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    evaluated_stage_ids: List[uuid.UUID],
    stage_name_map: Dict[uuid.UUID, str],
) -> List[FindingSpec]:
    """
    R007: Permitted Stage Reference Violation.
    Document in Stage A references a document in Stage B, but no ALLOWED_REFERENCE
    exists between Stage A and Stage B.
    """
    findings: List[FindingSpec] = []
    # Build set of allowed stage reference pairs: (consumer_stage_id, referenced_stage_id)
    allowed_refs = (
        db.query(StageReference)
        .filter(StageReference.stage_id.in_(evaluated_stage_ids))
        .all()
    )
    allowed_pairs = {(sr.stage_id, sr.references_stage_id) for sr in allowed_refs}

    # Query all REFERENCES edges originating in evaluated stages
    docs_in_scope = (
        db.query(Document)
        .filter(Document.project_id == project_id, Document.stage_id.in_(evaluated_stage_ids))
        .all()
    )
    doc_map = {d.document_id: d for d in docs_in_scope}
    if not doc_map:
        return findings

    doc_nodes = (
        db.query(Node)
        .filter(Node.source_table == "documents", Node.source_id.in_(list(doc_map.keys())))
        .all()
    )
    node_to_doc = {n.node_id: doc_map[n.source_id] for n in doc_nodes}

    ref_edges = (
        db.query(Edge)
        .filter(
            Edge.source_node_id.in_(list(node_to_doc.keys())),
            Edge.edge_type == "REFERENCES",
        )
        .all()
    )

    for edge in ref_edges:
        target_node = db.query(Node).filter(Node.node_id == edge.target_node_id).first()
        if target_node and target_node.source_table == "documents":
            target_doc = db.query(Document).filter(Document.document_id == target_node.source_id).first()
            if target_doc and target_doc.stage_id:
                consumer_doc = node_to_doc[edge.source_node_id]
                # Same stage is always allowed
                if consumer_doc.stage_id != target_doc.stage_id:
                    if (consumer_doc.stage_id, target_doc.stage_id) not in allowed_pairs:
                        stage_a_name = stage_name_map.get(consumer_doc.stage_id, "Stage A")
                        stage_b_name = stage_name_map.get(target_doc.stage_id, "Stage B")
                        findings.append(
                            FindingSpec(
                                rule_code="R007",
                                severity="HIGH",
                                is_blocker=True,
                                title="Cross-Stage Reference Violation",
                                description=f"Document '{consumer_doc.original_filename}' in '{stage_a_name}' references document '{target_doc.original_filename}' in '{stage_b_name}', but no permitted stage reference link exists from '{stage_a_name}' to '{stage_b_name}'.",
                                affected_entity_type="document",
                                affected_entity_id=consumer_doc.document_id,
                                target_stage_id=consumer_doc.stage_id,
                                details={
                                    "stage_name": stage_a_name,
                                    "entity_label": consumer_doc.original_filename,
                                    "referenced_stage": stage_b_name,
                                    "referenced_document": target_doc.original_filename,
                                },
                            )
                        )

    return findings


def evaluate_r008_unassigned_stage_requirements(
    db: Session,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    evaluated_stage_ids: List[uuid.UUID],
    stage_name_map: Dict[uuid.UUID, str],
) -> List[FindingSpec]:
    """
    R008: Unassigned Stage Requirement.
    Mandatory requirement exists in a stage where team_stage_access provides 0 teams with write access.
    """
    findings: List[FindingSpec] = []
    for stage_id in evaluated_stage_ids:
        # Check if stage has write-capable teams
        has_team_access = (
            db.query(TeamStageAccess)
            .filter(TeamStageAccess.stage_id == stage_id)
            .first()
        )
        if not has_team_access:
            mandatory_reqs = (
                db.query(RequiredDocument)
                .filter(RequiredDocument.stage_id == stage_id, RequiredDocument.is_mandatory.is_(True))
                .all()
            )
            for req in mandatory_reqs:
                stage_name = stage_name_map.get(stage_id, "Unknown Stage")
                findings.append(
                    FindingSpec(
                        rule_code="R008",
                        severity="MEDIUM",
                        is_blocker=False,
                        title="Unassigned Stage Requirement",
                        description=f"Mandatory requirement '{req.name}' is in stage '{stage_name}' which has no team assigned with write/upload access.",
                        affected_entity_type="requirement",
                        affected_entity_id=req.requirement_id,
                        target_stage_id=stage_id,
                        details={
                            "stage_name": stage_name,
                            "entity_label": req.name,
                        },
                    )
                )

    return findings


def evaluate_r009_document_contradictions(
    db: Session,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    evaluated_stage_ids: List[uuid.UUID],
    stage_name_map: Dict[uuid.UUID, str],
) -> List[FindingSpec]:
    """
    R009: Contradictory Statements Across Documents.
    Evaluates semantic claims extracted into knowledge.claims for documents within evaluated stages.
    Flags direct contradictions (differing values or opposing polarities) as high-severity blockers.
    """
    try:
        from app.services.graph.claims_analyzer import detect_project_contradictions
        return detect_project_contradictions(
            db=db,
            tenant_id=tenant_id,
            project_id=project_id,
            evaluated_stage_ids=evaluated_stage_ids,
        )
    except (ImportError, ModuleNotFoundError):
        return []


def evaluate_r010_pending_workflow_blockers(
    db: Session,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    evaluated_stage_ids: List[uuid.UUID],
    stage_name_map: Dict[uuid.UUID, str],
) -> List[FindingSpec]:
    """
    R010: Pending Workflow Blocker.
    For stages where requires_approval = True, documents that are still awaiting approval
    (status 'pending_review') generate an R010 blocker preventing the stage from exiting.
    Clearly distinct from R002 (which specifically flags unapproved draft or rejected documents).
    """
    findings: List[FindingSpec] = []
    gate_stages = (
        db.query(Stage)
        .filter(
            Stage.stage_id.in_(evaluated_stage_ids),
            Stage.requires_approval.is_(True),
            Stage.deleted_at.is_(None),
        )
        .all()
    )

    for stage in gate_stages:
        docs = (
            db.query(Document)
            .filter(Document.project_id == project_id, Document.stage_id == stage.stage_id)
            .all()
        )
        for doc in docs:
            wf = (
                db.query(WorkflowState)
                .filter(WorkflowState.document_id == doc.document_id)
                .first()
            )
            current_state = wf.state.value if (wf and hasattr(wf.state, "value")) else (wf.state if wf else "draft")
            if current_state in (WorkflowStatus.pending_review.value, "pending_review"):
                stage_name = stage_name_map.get(stage.stage_id, stage.name)
                findings.append(
                    FindingSpec(
                        rule_code="R010",
                        severity="HIGH",
                        is_blocker=True,
                        title="Pending Workflow Blocker",
                        description=f"Document '{doc.original_filename}' in approval-required stage '{stage_name}' is currently pending review and actively blocking stage exit.",
                        affected_entity_type="document",
                        affected_entity_id=doc.document_id,
                        target_stage_id=stage.stage_id,
                        details={
                            "stage_name": stage_name,
                            "entity_label": doc.original_filename,
                            "workflow_state": str(current_state),
                            "blocks_stage_exit": True,
                        },
                    )
                )

    return findings
