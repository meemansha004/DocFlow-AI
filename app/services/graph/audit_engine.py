import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from sqlalchemy.orm import Session

from app.models.graph import (
    AuditFinding,
    AuditRun,
    Edge,
    Node,
    ProjectMetricSnapshot,
    StageMetricSnapshot,
)
from app.models.project import Project
from app.models.stage import Stage
from app.models.required_document import RequiredDocument
from app.services.graph.audit_rules import (
    FindingSpec,
    evaluate_r001_missing_mandatory_requirements,
    evaluate_r002_unapproved_documents_in_gate_stages,
    evaluate_r003_broken_stage_dependencies,
    evaluate_r004_stale_document_references,
    evaluate_r005_dependency_cycles,
    evaluate_r006_orphan_entities,
    evaluate_r007_permitted_stage_reference_violations,
    evaluate_r008_unassigned_stage_requirements,
    evaluate_r009_document_contradictions,
    evaluate_r010_pending_workflow_blockers,
)

AUDIT_RULES_VERSION = "1.0.0"

# Explicit inspectable rule registry for deterministic project audit
RULE_REGISTRY = [
    ("R001", evaluate_r001_missing_mandatory_requirements),
    ("R002", evaluate_r002_unapproved_documents_in_gate_stages),
    ("R003", evaluate_r003_broken_stage_dependencies),
    ("R004", evaluate_r004_stale_document_references),
    ("R005", evaluate_r005_dependency_cycles),
    ("R006", evaluate_r006_orphan_entities),
    ("R007", evaluate_r007_permitted_stage_reference_violations),
    ("R008", evaluate_r008_unassigned_stage_requirements),
    ("R009", evaluate_r009_document_contradictions),
    ("R010", evaluate_r010_pending_workflow_blockers),
]



def get_upstream_stage_ids(
    db: Session, project_id: uuid.UUID, target_stage_id: uuid.UUID
) -> List[uuid.UUID]:
    """
    Directional traversal: gathers target stage S and all upstream ancestors U
    reachable via inverted PRECEDES edges (where U precedes ... precedes S).
    Strictly excludes future/downstream descendants.
    """
    # Active stages ordered by order_index
    active_stages = (
        db.query(Stage)
        .filter(Stage.project_id == project_id, Stage.deleted_at.is_(None))
        .order_by(Stage.order_index.asc(), Stage.created_at.asc())
        .all()
    )
    target_idx: Optional[int] = None
    for i, st in enumerate(active_stages):
        if st.stage_id == target_stage_id:
            target_idx = i
            break

    if target_idx is None:
        # Fallback if target stage not found in active list
        return [target_stage_id]

    # All stages up to and including target_idx are upstream or current
    return [s.stage_id for s in active_stages[: target_idx + 1]]


def execute_project_audit(
    db: Session,
    project_id: uuid.UUID,
    target_stage_id: Optional[uuid.UUID] = None,
    triggered_by: Optional[uuid.UUID] = None,
) -> AuditRun:
    """
    Executes a deterministic, lifecycle-aware, stage-scoped audit.
    Generates immutable AuditRun, AuditFinding, and MetricSnapshots.
    """
    project = db.query(Project).filter(Project.project_id == project_id).first()
    if not project:
        raise ValueError(f"Project {project_id} not found")

    tenant_id = project.tenant_id

    # 1. Capture Immutable Lifecycle Snapshot
    active_stages = (
        db.query(Stage)
        .filter(Stage.project_id == project_id, Stage.deleted_at.is_(None))
        .order_by(Stage.order_index.asc(), Stage.created_at.asc())
        .all()
    )
    lifecycle_snapshot = {
        "stages": [
            {
                "stage_id": str(s.stage_id),
                "name": s.name,
                "order_index": s.order_index,
                "requires_approval": s.requires_approval,
            }
            for s in active_stages
        ],
        "audit_scope": str(target_stage_id) if target_stage_id else "full_project",
    }
    stage_name_map = {s.stage_id: s.name for s in active_stages}

    # 2. Determine Directional Evaluation Scope
    if target_stage_id:
        evaluated_stage_ids = get_upstream_stage_ids(db, project_id, target_stage_id)
    else:
        evaluated_stage_ids = [s.stage_id for s in active_stages]

    # 3. Evaluate Deterministic Rules via Explicit Registry
    all_findings: List[FindingSpec] = []
    rules_evaluated_count = len(RULE_REGISTRY)

    for rule_code, rule_fn in RULE_REGISTRY:
        rule_findings = rule_fn(
            db=db,
            tenant_id=tenant_id,
            project_id=project_id,
            evaluated_stage_ids=evaluated_stage_ids,
            stage_name_map=stage_name_map,
        )
        all_findings.extend(rule_findings)


    # 4. Compute Metrics: Completeness (Continuous) vs Readiness (Hard Gate)
    blockers = [f for f in all_findings if f.is_blocker]
    blockers_count = len(blockers)
    readiness_status = "READY" if blockers_count == 0 else "NOT_READY"

    # Completeness calculation based on mandatory requirement satisfaction
    total_reqs = (
        db.query(RequiredDocument)
        .filter(
            RequiredDocument.stage_id.in_(evaluated_stage_ids),
            RequiredDocument.is_mandatory.is_(True),
        )
        .count()
    )
    missing_reqs_count = len([f for f in all_findings if f.rule_code == "R001"])
    satisfied_reqs_count = max(0, total_reqs - missing_reqs_count)

    if total_reqs > 0:
        completeness_score = round((satisfied_reqs_count / total_reqs) * 100.0, 1)
    else:
        completeness_score = 100.0

    severity_counts = {
        "CRITICAL": len([f for f in all_findings if f.severity == "CRITICAL"]),
        "HIGH": len([f for f in all_findings if f.severity == "HIGH"]),
        "MEDIUM": len([f for f in all_findings if f.severity == "MEDIUM"]),
        "LOW": len([f for f in all_findings if f.severity == "LOW"]),
    }

    # 5. Persist AuditRun
    audit_run = AuditRun(
        tenant_id=tenant_id,
        project_id=project_id,
        target_stage_id=target_stage_id,
        lifecycle_snapshot=lifecycle_snapshot,
        rules_version=AUDIT_RULES_VERSION,
        triggered_by=triggered_by,
        status="completed",
        rules_evaluated=rules_evaluated_count,
        findings_count=len(all_findings),
        readiness_status=readiness_status,
        completeness_score=completeness_score,
        summary={
            "blockers_count": blockers_count,
            "severity_breakdown": severity_counts,
            "total_mandatory_requirements": total_reqs,
            "satisfied_mandatory_requirements": satisfied_reqs_count,
        },
        completed_at=datetime.now(timezone.utc),
    )
    db.add(audit_run)
    db.flush()

    # 6. Persist Self-Contained AuditFindings
    for f in all_findings:
        finding = AuditFinding(
            run_id=audit_run.run_id,
            tenant_id=tenant_id,
            project_id=project_id,
            target_stage_id=f.target_stage_id,
            rule_code=f.rule_code,
            severity=f.severity,
            is_blocker=f.is_blocker,
            title=f.title,
            description=f.description,
            affected_entity_type=f.affected_entity_type,
            affected_entity_id=f.affected_entity_id,
            evidence_sources=f.evidence_sources,
            details=f.details,
        )
        db.add(finding)

    # 7. Persist Historical Project & Stage Metric Snapshots
    project_snapshot = ProjectMetricSnapshot(
        tenant_id=tenant_id,
        project_id=project_id,
        audit_run_id=audit_run.run_id,
        completeness_score=completeness_score,
        readiness_status=readiness_status,
        mandatory_requirement_coverage=(satisfied_reqs_count / total_reqs) if total_reqs > 0 else 1.0,
        approval_health=1.0 if len([f for f in all_findings if f.rule_code in ("R002", "R010")]) == 0 else 0.5,
        dependency_health=1.0 if len([f for f in all_findings if f.rule_code in ("R003", "R005", "R007")]) == 0 else 0.0,
        document_health=1.0 if len([f for f in all_findings if f.rule_code == "R006"]) == 0 else 0.5,
        version_reference_health=1.0 if len([f for f in all_findings if f.rule_code == "R004"]) == 0 else 0.5,
        conflict_health=1.0 if len([f for f in all_findings if f.rule_code == "R009"]) == 0 else 0.0,
        open_findings_by_severity=severity_counts,
        blockers_count=blockers_count,
    )
    db.add(project_snapshot)

    for st_id in evaluated_stage_ids:
        stage_blockers = [f for f in blockers if f.target_stage_id == st_id]
        stage_reqs_total = (
            db.query(RequiredDocument)
            .filter(RequiredDocument.stage_id == st_id, RequiredDocument.is_mandatory.is_(True))
            .count()
        )
        stage_reqs_missing = len([f for f in all_findings if f.target_stage_id == st_id and f.rule_code == "R001"])
        stage_satisfied = max(0, stage_reqs_total - stage_reqs_missing)
        stage_comp = round((stage_satisfied / stage_reqs_total) * 100.0, 1) if stage_reqs_total > 0 else 100.0

        stage_snapshot = StageMetricSnapshot(
            tenant_id=tenant_id,
            project_id=project_id,
            stage_id=st_id,
            stage_name=stage_name_map.get(st_id, "Unknown Stage"),
            audit_run_id=audit_run.run_id,
            completeness_score=stage_comp,
            readiness_status="READY" if len(stage_blockers) == 0 else "NOT_READY",
            mandatory_requirements_total=stage_reqs_total,
            mandatory_requirements_satisfied=stage_satisfied,
            mandatory_requirements_missing=stage_reqs_missing,
            mandatory_requirements_partial=0,
            mandatory_requirements_blocked=len(stage_blockers),
            upstream_requirements_applicable=total_reqs - stage_reqs_total,
            upstream_requirements_satisfied=max(0, (total_reqs - stage_reqs_total) - (missing_reqs_count - stage_reqs_missing)),
            evidence_coverage=1.0 if stage_reqs_missing == 0 else 0.0,
            document_health=1.0 if len([f for f in all_findings if f.target_stage_id == st_id and f.rule_code == "R006"]) == 0 else 0.5,
            approvals_satisfied=len([f for f in all_findings if f.target_stage_id == st_id and f.rule_code in ("R002", "R010")]) == 0,
            blockers_count=len(stage_blockers),
        )

        db.add(stage_snapshot)

    db.commit()
    return audit_run
