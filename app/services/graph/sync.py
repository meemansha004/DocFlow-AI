import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import and_, select, delete
from sqlalchemy.orm import Session

from app.models.graph import Edge, Node
from app.models.project import Project
from app.models.stage import Stage, StageReference, TeamStageAccess
from app.models.document import Document
from app.models.required_document import RequiredDocument
from app.models.team import Team, ProjectAdmin, UserTeamMembership
from app.models.user import User


class SyncResult:
    def __init__(self, project_id: uuid.UUID):
        self.project_id = project_id
        self.nodes_synced: int = 0
        self.edges_synced: int = 0
        self.errors: List[str] = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": str(self.project_id),
            "nodes_synced": self.nodes_synced,
            "edges_synced": self.edges_synced,
            "errors": self.errors,
        }


def _upsert_node(
    db: Session,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    entity_type: str,
    source_table: str,
    source_id: uuid.UUID,
    label: str,
    properties: Optional[Dict[str, Any]] = None,
) -> Node:
    """Idempotently insert or update a graph node based on (tenant_id, project_id, source_table, source_id)."""
    node = db.query(Node).filter(
        Node.tenant_id == tenant_id,
        Node.project_id == project_id,
        Node.source_table == source_table,
        Node.source_id == source_id,
    ).first()

    props = properties or {}
    if node:
        node.label = label
        node.project_id = project_id
        node.properties = props
        node.updated_at = datetime.now(timezone.utc)
    else:
        node = Node(
            tenant_id=tenant_id,
            project_id=project_id,
            entity_type=entity_type,
            source_table=source_table,
            source_id=source_id,
            label=label,
            properties=props,
        )
        db.add(node)
        db.flush()

    return node


def _upsert_edge(
    db: Session,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    source_node_id: uuid.UUID,
    target_node_id: uuid.UUID,
    edge_type: str,
    properties: Optional[Dict[str, Any]] = None,
    confidence: float = 1.0,
    provenance: Optional[Dict[str, Any]] = None,
) -> Edge:
    """Idempotently insert or update a graph edge based on (source_node_id, target_node_id, edge_type)."""
    edge = db.query(Edge).filter(
        Edge.source_node_id == source_node_id,
        Edge.target_node_id == target_node_id,
        Edge.edge_type == edge_type,
    ).first()

    props = properties or {}
    if edge:
        edge.properties = props
        edge.confidence = confidence
        edge.provenance = provenance
    else:
        edge = Edge(
            tenant_id=tenant_id,
            project_id=project_id,
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            edge_type=edge_type,
            properties=props,
            confidence=confidence,
            provenance=provenance,
        )
        db.add(edge)
        db.flush()

    return edge


def sync_project_graph(db: Session, project_id: uuid.UUID) -> SyncResult:
    """
    Synchronizes all authoritative relational entities and dynamic stage topology
    for a given project into the 'knowledge.*' graph tables.
    """
    result = SyncResult(project_id)
    project = db.query(Project).filter(Project.project_id == project_id).first()
    if not project:
        result.errors.append(f"Project {project_id} not found")
        return result

    tenant_id = project.tenant_id
    node_map: Dict[Tuple[str, uuid.UUID], Node] = {}

    # 1. Sync Project Node
    proj_node = _upsert_node(
        db=db,
        tenant_id=tenant_id,
        project_id=project_id,
        entity_type="project",
        source_table="projects",
        source_id=project.project_id,
        label=project.name,
        properties={"status": getattr(project, "status", "active")},
    )
    node_map[("projects", project.project_id)] = proj_node
    result.nodes_synced += 1

    # 2. Sync Active Stages & Dynamic Topology
    # Non-deleted stages ordered by (order_index ASC, created_at ASC)
    active_stages = (
        db.query(Stage)
        .filter(Stage.project_id == project_id, Stage.deleted_at.is_(None))
        .order_by(Stage.order_index.asc(), Stage.created_at.asc())
        .all()
    )

    stage_nodes: List[Node] = []
    for stage in active_stages:
        s_node = _upsert_node(
            db=db,
            tenant_id=tenant_id,
            project_id=project_id,
            entity_type="stage",
            source_table="stages",
            source_id=stage.stage_id,
            label=stage.name,
            properties={
                "order_index": stage.order_index,
                "requires_approval": stage.requires_approval,
            },
        )
        stage_nodes.append(s_node)
        node_map[("stages", stage.stage_id)] = s_node
        result.nodes_synced += 1

        # Project -> CONTAINS_STAGE -> Stage
        _upsert_edge(
            db=db,
            tenant_id=tenant_id,
            project_id=project_id,
            source_node_id=proj_node.node_id,
            target_node_id=s_node.node_id,
            edge_type="CONTAINS_STAGE",
        )
        result.edges_synced += 1

    # Synchronize Dynamic PRECEDES Edges (Immediate adjacency only: Stage_i -> Stage_{i+1})
    # First, collect valid new PRECEDES pairs
    active_precedes_pairs: Set[Tuple[uuid.UUID, uuid.UUID]] = set()
    for i in range(len(stage_nodes) - 1):
        prev_node = stage_nodes[i]
        next_node = stage_nodes[i + 1]
        active_precedes_pairs.add((prev_node.node_id, next_node.node_id))
        _upsert_edge(
            db=db,
            tenant_id=tenant_id,
            project_id=project_id,
            source_node_id=prev_node.node_id,
            target_node_id=next_node.node_id,
            edge_type="PRECEDES",
            properties={"immediate": True},
        )
        result.edges_synced += 1

    # Remove stale PRECEDES edges for this project (e.g. from prior stage ordering or archived stages)
    current_precedes_edges = (
        db.query(Edge)
        .filter(Edge.project_id == project_id, Edge.edge_type == "PRECEDES")
        .all()
    )
    for edge in current_precedes_edges:
        if (edge.source_node_id, edge.target_node_id) not in active_precedes_pairs:
            db.delete(edge)

    # 3. Sync Permitted Cross-Stage References (ALLOWED_REFERENCE)
    # stage_references(stage_id, references_stage_id) -> (Stage: stage_id) -[ALLOWED_REFERENCE]-> (Stage: references_stage_id)
    stage_ids = [s.stage_id for s in active_stages]
    if stage_ids:
        stage_refs = (
            db.query(StageReference)
            .filter(StageReference.stage_id.in_(stage_ids))
            .all()
        )
        for sref in stage_refs:
            source_node = node_map.get(("stages", sref.stage_id))
            target_node = node_map.get(("stages", sref.references_stage_id))
            if source_node and target_node:
                _upsert_edge(
                    db=db,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    source_node_id=source_node.node_id,
                    target_node_id=target_node.node_id,
                    edge_type="ALLOWED_REFERENCE",
                )
                result.edges_synced += 1

    # 4. Sync Requirements (required_documents)
    reqs = (
        db.query(RequiredDocument)
        .filter(RequiredDocument.stage_id.in_(stage_ids))
        .all()
    )
    for req in reqs:
        r_node = _upsert_node(
            db=db,
            tenant_id=tenant_id,
            project_id=project_id,
            entity_type="requirement",
            source_table="required_documents",
            source_id=req.requirement_id,
            label=req.name,
            properties={
                "is_mandatory": req.is_mandatory,
                "description": req.description,
                "source": str(req.source) if req.source else "custom",
            },
        )
        node_map[("required_documents", req.requirement_id)] = r_node
        result.nodes_synced += 1

        # Stage -> REQUIRES -> Requirement
        stage_node = node_map.get(("stages", req.stage_id))
        if stage_node:
            _upsert_edge(
                db=db,
                tenant_id=tenant_id,
                project_id=project_id,
                source_node_id=stage_node.node_id,
                target_node_id=r_node.node_id,
                edge_type="REQUIRES",
                properties={"is_mandatory": req.is_mandatory},
            )
            result.edges_synced += 1

            # Requirement -> ORIGINATED_IN -> Stage
            _upsert_edge(
                db=db,
                tenant_id=tenant_id,
                project_id=project_id,
                source_node_id=r_node.node_id,
                target_node_id=stage_node.node_id,
                edge_type="ORIGINATED_IN",
            )
            result.edges_synced += 1

            # Deterministic default: Requirement -> APPLIES_TO -> Stage (originating stage)
            _upsert_edge(
                db=db,
                tenant_id=tenant_id,
                project_id=project_id,
                source_node_id=r_node.node_id,
                target_node_id=stage_node.node_id,
                edge_type="APPLIES_TO",
                properties={"rule": "originating_default"},
            )
            result.edges_synced += 1

    # 5. Sync Teams & Team Stage Access
    teams = db.query(Team).filter(Team.project_id == project_id).all()
    for team in teams:
        t_node = _upsert_node(
            db=db,
            tenant_id=tenant_id,
            project_id=project_id,
            entity_type="team",
            source_table="teams",
            source_id=team.team_id,
            label=team.name,
        )
        node_map[("teams", team.team_id)] = t_node
        result.nodes_synced += 1

    # Team -> ASSIGNED_TEAM -> Stage
    team_stage_accesses = (
        db.query(TeamStageAccess)
        .filter(TeamStageAccess.stage_id.in_(stage_ids))
        .all()
    )
    for tsa in team_stage_accesses:
        t_node = node_map.get(("teams", tsa.team_id))
        s_node = node_map.get(("stages", tsa.stage_id))
        if t_node and s_node:
            _upsert_edge(
                db=db,
                tenant_id=tenant_id,
                project_id=project_id,
                source_node_id=t_node.node_id,
                target_node_id=s_node.node_id,
                edge_type="ASSIGNED_TEAM",
                properties={"access_granted": True},
            )
            result.edges_synced += 1

    # 6. Sync Users & Project Admins
    admins = db.query(ProjectAdmin).filter(ProjectAdmin.project_id == project_id).all()
    for pa in admins:
        user = db.query(User).filter(User.user_id == pa.user_id).first()
        if user:
            u_node = _upsert_node(
                db=db,
                tenant_id=tenant_id,
                project_id=project_id,
                entity_type="user",
                source_table="users",
                source_id=user.user_id,
                label=user.full_name or user.email,
            )
            node_map[("users", user.user_id)] = u_node
            result.nodes_synced += 1

            _upsert_edge(
                db=db,
                tenant_id=tenant_id,
                project_id=project_id,
                source_node_id=u_node.node_id,
                target_node_id=proj_node.node_id,
                edge_type="MANAGES_PROJECT",
                properties={"role": "admin"},
            )
            result.edges_synced += 1

    # Sync project team members
    memberships = db.query(UserTeamMembership).filter(UserTeamMembership.project_id == project_id).all()
    for m in memberships:
        user = db.query(User).filter(User.user_id == m.user_id).first()
        if user:
            u_node = _upsert_node(
                db=db,
                tenant_id=tenant_id,
                project_id=project_id,
                entity_type="user",
                source_table="users",
                source_id=user.user_id,
                label=user.full_name or user.email,
            )
            node_map[("users", user.user_id)] = u_node
            result.nodes_synced += 1

            t_node = node_map.get(("teams", m.team_id))
            if t_node:
                _upsert_edge(
                    db=db,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    source_node_id=u_node.node_id,
                    target_node_id=t_node.node_id,
                    edge_type="MEMBER_OF",
                    properties={"role": m.role.value if hasattr(m.role, "value") else str(m.role)},
                )
                result.edges_synced += 1


    # 7. Sync Documents
    docs = (
        db.query(Document)
        .filter(Document.project_id == project_id)
        .all()
    )
    for doc in docs:
        d_node = _upsert_node(
            db=db,
            tenant_id=tenant_id,
            project_id=project_id,
            entity_type="document",
            source_table="documents",
            source_id=doc.document_id,
            label=doc.original_filename or "Untitled Document",
            properties={
                "current_version_id": str(doc.current_version_id) if doc.current_version_id else None,
                "sensitivity": getattr(doc, "sensitivity_level", None),
                "original_filename": doc.original_filename,
            },
        )
        node_map[("documents", doc.document_id)] = d_node
        result.nodes_synced += 1

        # Document -> BELONGS_TO_STAGE -> Stage
        if doc.stage_id:
            s_node = node_map.get(("stages", doc.stage_id))
            if s_node:
                _upsert_edge(
                    db=db,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    source_node_id=d_node.node_id,
                    target_node_id=s_node.node_id,
                    edge_type="BELONGS_TO_STAGE",
                )
                result.edges_synced += 1

        # Document -> OWNED_BY -> Team
        if doc.uploaded_as_team_id:
            t_node = node_map.get(("teams", doc.uploaded_as_team_id))
            if t_node:
                _upsert_edge(
                    db=db,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    source_node_id=d_node.node_id,
                    target_node_id=t_node.node_id,
                    edge_type="OWNED_BY",
                )
                result.edges_synced += 1

    db.commit()
    return result
