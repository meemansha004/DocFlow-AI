import uuid
from typing import Any, Dict, List, Optional, Set
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.database import get_db
from app.models.graph import AuditFinding, AuditRun, Edge, Node, ProjectMetricSnapshot
from app.models.project import Project
from app.models.stage import Stage
from app.schemas.graph import (
    AuditRunResponse,
    FindingDTO,
    FindingsResponse,
    GapsResponse,
    GraphEdgeDTO,
    GraphNodeDTO,
    NeighborhoodResponse,
    ProgressHistoryResponse,
    ProjectMetricDTO,
    ProjectMetricsResponse,
    StageMetricDTO,
    TimelineItemDTO,
    TimelineResponse,
    TriggerAuditRequest,
)
from app.services.access_control import (
    get_accessible_stages_for_user,
    has_any_project_access,
)
from app.services.auth import ResolvedIdentity
from app.services.graph.audit_engine import execute_project_audit
from app.services.graph.metrics_service import (
    get_latest_project_health,
    get_project_progress_history,
    get_project_timeline,
)

router = APIRouter(prefix="/projects/{project_id}/intelligence", tags=["project-intelligence"])


def _check_project_access(db: Session, identity: ResolvedIdentity, project_id: uuid.UUID) -> Set[uuid.UUID]:
    project = db.query(Project).filter(Project.project_id == project_id).first()
    if not project or project.tenant_id != identity.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    if not has_any_project_access(db, identity.user_id, project_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this project",
        )

    accessible_stages = set(get_accessible_stages_for_user(db, identity.user_id, project_id))
    return accessible_stages


@router.get("/metrics", response_model=ProjectMetricsResponse)
def get_metrics(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    identity: ResolvedIdentity = Depends(get_current_user),
):
    """
    Returns the latest project health metrics, readiness status, and stage-by-stage health.
    Stages outside the caller's team access are redacted.
    """
    accessible_stages = _check_project_access(db, identity, project_id)
    health = get_latest_project_health(db, project_id)

    if not health or not health["project_metric"]:
        # If no audit run has executed yet, trigger one synchronously
        audit_run = execute_project_audit(db, project_id, triggered_by=identity.user_id)
        health = get_latest_project_health(db, project_id)

    if not health:
        return ProjectMetricsResponse(project_id=str(project_id), project_metric=None, stages=[])

    pm = health["project_metric"]
    project_metric_dto = ProjectMetricDTO(**pm)

    # Redact stages not accessible to user
    visible_stages = [
        StageMetricDTO(**s)
        for s in health["stages"]
        if uuid.UUID(s["stage_id"]) in accessible_stages
    ]

    return ProjectMetricsResponse(
        project_id=str(project_id),
        project_metric=project_metric_dto,
        stages=visible_stages,
    )


@router.get("/gaps", response_model=GapsResponse)
def get_gaps(
    project_id: uuid.UUID,
    stage_id: Optional[uuid.UUID] = Query(None, description="Optional target stage scope"),
    db: Session = Depends(get_db),
    identity: ResolvedIdentity = Depends(get_current_user),
):
    """
    Returns current project blockers and gaps partitioned by gap type
    (missing requirements, broken dependencies, reference breaches, gate approvals, contradictions).
    """
    accessible_stages = _check_project_access(db, identity, project_id)
    if stage_id and stage_id not in accessible_stages:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access to specified stage is denied",
        )

    # Execute deterministic audit evaluation
    audit_run = execute_project_audit(db, project_id, target_stage_id=stage_id, triggered_by=identity.user_id)

    # Filter findings to accessible stages
    visible_findings = [
        f for f in audit_run.findings
        if f.target_stage_id is None or f.target_stage_id in accessible_stages
    ]

    missing_reqs: List[FindingDTO] = []
    broken_deps: List[FindingDTO] = []
    ref_violations: List[FindingDTO] = []
    unapproved_gate: List[FindingDTO] = []
    contradictions: List[FindingDTO] = []

    for f in visible_findings:
        dto = FindingDTO(
            finding_id=str(f.finding_id),
            rule_code=f.rule_code,
            severity=f.severity,
            is_blocker=f.is_blocker,
            title=f.title,
            description=f.description,
            affected_entity_type=f.affected_entity_type,
            affected_entity_id=str(f.affected_entity_id),
            target_stage_id=str(f.target_stage_id) if f.target_stage_id else None,
            evidence_sources=f.evidence_sources,
            details=f.details,
            created_at=f.created_at.isoformat() if f.created_at else None,
        )
        if f.rule_code == "R001":
            missing_reqs.append(dto)
        elif f.rule_code in ("R003", "R005"):
            broken_deps.append(dto)
        elif f.rule_code == "R007":
            ref_violations.append(dto)
        elif f.rule_code == "R002":
            unapproved_gate.append(dto)
        elif f.rule_code == "R009":
            contradictions.append(dto)

    total_blockers = len([f for f in visible_findings if f.is_blocker])

    return GapsResponse(
        project_id=str(project_id),
        target_stage_id=str(stage_id) if stage_id else None,
        readiness_status=audit_run.readiness_status,
        completeness_score=audit_run.completeness_score,
        total_blockers=total_blockers,
        missing_mandatory_requirements=missing_reqs,
        broken_dependencies=broken_deps,
        permitted_reference_violations=ref_violations,
        unapproved_gate_documents=unapproved_gate,
        contradictions=contradictions,
    )


@router.get("/findings", response_model=FindingsResponse)
def get_findings(
    project_id: uuid.UUID,
    severity: Optional[str] = Query(None, description="Filter by severity (HIGH, MEDIUM, LOW)"),
    rule_code: Optional[str] = Query(None, description="Filter by rule code (e.g. R001, R002)"),
    is_blocker: Optional[bool] = Query(None, description="Filter by blocker status"),
    stage_id: Optional[uuid.UUID] = Query(None, description="Filter by target stage"),
    db: Session = Depends(get_db),
    identity: ResolvedIdentity = Depends(get_current_user),
):
    """
    Returns filtered list of active audit findings.
    """
    accessible_stages = _check_project_access(db, identity, project_id)

    # Find the latest audit run
    latest_run = (
        db.query(AuditRun)
        .filter(AuditRun.project_id == project_id)
        .order_by(AuditRun.started_at.desc())
        .first()
    )
    if not latest_run:
        latest_run = execute_project_audit(db, project_id, triggered_by=identity.user_id)

    query = db.query(AuditFinding).filter(AuditFinding.run_id == latest_run.run_id)

    if severity:
        query = query.filter(AuditFinding.severity == severity.upper())
    if rule_code:
        query = query.filter(AuditFinding.rule_code == rule_code.upper())
    if is_blocker is not None:
        query = query.filter(AuditFinding.is_blocker == is_blocker)
    if stage_id:
        query = query.filter(AuditFinding.target_stage_id == stage_id)

    raw_findings = query.all()

    # Apply ABAC stage filtering
    visible_findings = [
        f for f in raw_findings
        if f.target_stage_id is None or f.target_stage_id in accessible_stages
    ]

    dtos = [
        FindingDTO(
            finding_id=str(f.finding_id),
            rule_code=f.rule_code,
            severity=f.severity,
            is_blocker=f.is_blocker,
            title=f.title,
            description=f.description,
            affected_entity_type=f.affected_entity_type,
            affected_entity_id=str(f.affected_entity_id),
            target_stage_id=str(f.target_stage_id) if f.target_stage_id else None,
            evidence_sources=f.evidence_sources,
            details=f.details,
            created_at=f.created_at.isoformat() if f.created_at else None,
        )
        for f in visible_findings
    ]

    blockers_count = len([f for f in visible_findings if f.is_blocker])

    return FindingsResponse(
        project_id=str(project_id),
        total_findings=len(dtos),
        blockers_count=blockers_count,
        findings=dtos,
    )


@router.get("/neighborhood", response_model=NeighborhoodResponse)
def get_neighborhood(
    project_id: uuid.UUID,
    node_id: Optional[uuid.UUID] = Query(None),
    source_table: Optional[str] = Query(None),
    source_id: Optional[uuid.UUID] = Query(None),
    depth: int = Query(1, ge=1, le=3),
    db: Session = Depends(get_db),
    identity: ResolvedIdentity = Depends(get_current_user),
):
    """
    Returns subgraph neighborhood around a central entity node for interactive visualization.
    """
    accessible_stages = _check_project_access(db, identity, project_id)

    center_node: Optional[Node] = None
    if node_id:
        center_node = (
            db.query(Node)
            .filter(Node.node_id == node_id, Node.project_id == project_id)
            .first()
        )
    elif source_table and source_id:
        center_node = (
            db.query(Node)
            .filter(
                Node.source_table == source_table,
                Node.source_id == source_id,
                Node.project_id == project_id,
            )
            .first()
        )

    if not center_node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Center node not found in project graph",
        )

    visited_nodes: Dict[uuid.UUID, Node] = {center_node.node_id: center_node}
    collected_edges: Dict[uuid.UUID, Edge] = {}
    current_frontier = {center_node.node_id}

    for _ in range(depth):
        if not current_frontier:
            break

        edges = (
            db.query(Edge)
            .filter(
                Edge.project_id == project_id,
                (Edge.source_node_id.in_(current_frontier) | Edge.target_node_id.in_(current_frontier)),
            )
            .all()
        )

        next_frontier = set()
        for e in edges:
            collected_edges[e.edge_id] = e
            for nid in (e.source_node_id, e.target_node_id):
                if nid not in visited_nodes:
                    next_frontier.add(nid)

        if next_frontier:
            new_nodes = (
                db.query(Node)
                .filter(Node.node_id.in_(next_frontier), Node.project_id == project_id)
                .all()
            )
            for n in new_nodes:
                visited_nodes[n.node_id] = n
            current_frontier = next_frontier
        else:
            break

    # Apply ABAC filtering: if node is a stage or document, check user access
    def is_visible(node: Node) -> bool:
        if node.source_table == "stages":
            return node.source_id in accessible_stages
        if node.source_table == "documents":
            stage_id = node.properties.get("stage_id")
            if stage_id:
                return uuid.UUID(stage_id) in accessible_stages
        return True

    filtered_node_ids = {nid for nid, n in visited_nodes.items() if is_visible(n)}

    node_dtos = [
        GraphNodeDTO(
            node_id=str(n.node_id),
            entity_type=n.entity_type,
            source_table=n.source_table,
            source_id=str(n.source_id),
            label=n.label,
            properties=n.properties,
        )
        for nid, n in visited_nodes.items()
        if nid in filtered_node_ids
    ]

    edge_dtos = [
        GraphEdgeDTO(
            edge_id=str(e.edge_id),
            source_node_id=str(e.source_node_id),
            target_node_id=str(e.target_node_id),
            edge_type=e.edge_type,
            confidence=e.confidence,
            properties=e.properties,
        )
        for e in collected_edges.values()
        if e.source_node_id in filtered_node_ids and e.target_node_id in filtered_node_ids
    ]

    center_dto = None
    if center_node.node_id in filtered_node_ids:
        center_dto = GraphNodeDTO(
            node_id=str(center_node.node_id),
            entity_type=center_node.entity_type,
            source_table=center_node.source_table,
            source_id=str(center_node.source_id),
            label=center_node.label,
            properties=center_node.properties,
        )

    return NeighborhoodResponse(
        center_node=center_dto,
        nodes=node_dtos,
        edges=edge_dtos,
    )


@router.get("/progress-history", response_model=ProgressHistoryResponse)
def get_progress_history(
    project_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    identity: ResolvedIdentity = Depends(get_current_user),
):
    """
    Returns time-series history of project progress metrics.
    """
    _check_project_access(db, identity, project_id)
    history = get_project_progress_history(db, project_id, limit=limit)
    return ProgressHistoryResponse(project_id=str(project_id), history=history)


@router.get("/timeline", response_model=TimelineResponse)
def get_timeline(
    project_id: uuid.UUID,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    identity: ResolvedIdentity = Depends(get_current_user),
):
    """
    Returns synthesized project lifecycle events (audit runs, approvals, activities).
    """
    _check_project_access(db, identity, project_id)
    items = get_project_timeline(db, project_id, limit=limit)
    timeline_dtos = [TimelineItemDTO(**item) for item in items]
    return TimelineResponse(project_id=str(project_id), timeline=timeline_dtos)


@router.post("/audit", response_model=AuditRunResponse)
def trigger_audit(
    project_id: uuid.UUID,
    body: TriggerAuditRequest = TriggerAuditRequest(),
    db: Session = Depends(get_db),
    identity: ResolvedIdentity = Depends(get_current_user),
):
    """
    Triggers an on-demand deterministic audit run for the project or target stage.
    """
    accessible_stages = _check_project_access(db, identity, project_id)
    if body.target_stage_id and body.target_stage_id not in accessible_stages:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access to specified stage is denied",
        )

    audit_run = execute_project_audit(
        db,
        project_id,
        target_stage_id=body.target_stage_id,
        triggered_by=identity.user_id,
    )

    return AuditRunResponse(
        run_id=str(audit_run.run_id),
        project_id=str(audit_run.project_id),
        target_stage_id=str(audit_run.target_stage_id) if audit_run.target_stage_id else None,
        readiness_status=audit_run.readiness_status,
        completeness_score=audit_run.completeness_score,
        rules_evaluated=audit_run.rules_evaluated,
        blockers_count=audit_run.summary.get("blockers_count", len([f for f in audit_run.findings if f.is_blocker])),
        findings_count=audit_run.findings_count,
        started_at=audit_run.started_at.isoformat(),
        completed_at=audit_run.completed_at.isoformat() if audit_run.completed_at else None,
    )
