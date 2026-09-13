import json
import uuid
from typing import Any, Dict, List, Optional

from app.database import SessionLocal
from app.models.graph import Edge, Node
from app.models.project import Project
from app.services.graph.audit_engine import execute_project_audit
from app.services.graph.metrics_service import (
    get_latest_project_health,
    get_project_progress_history,
    get_project_timeline,
)


def query_project_readiness(project_id: str, stage_id: Optional[str] = None) -> str:
    """
    Agent Tool: Queries the deterministic readiness status and completeness score of a project or target stage.
    """
    db = SessionLocal()
    try:
        p_uuid = uuid.UUID(project_id)
        s_uuid = uuid.UUID(stage_id) if stage_id else None

        audit_run = execute_project_audit(db, p_uuid, target_stage_id=s_uuid)
        blockers = [f for f in audit_run.findings if f.is_blocker]

        summary = {
            "project_id": project_id,
            "target_stage_id": stage_id,
            "readiness_status": audit_run.readiness_status,
            "completeness_score": f"{audit_run.completeness_score}%",
            "total_blockers": len(blockers),
            "blockers_summary": [
                {
                    "rule_code": b.rule_code,
                    "title": b.title,
                    "description": b.description,
                    "affected_entity_type": b.affected_entity_type,
                }
                for b in blockers[:5]
            ],
        }
        return json.dumps(summary, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})
    finally:
        db.close()


def query_project_gaps(project_id: str, stage_id: Optional[str] = None) -> str:
    """
    Agent Tool: Queries active compliance gaps, missing requirements, gate blocks, and contradictions.
    """
    db = SessionLocal()
    try:
        p_uuid = uuid.UUID(project_id)
        s_uuid = uuid.UUID(stage_id) if stage_id else None

        audit_run = execute_project_audit(db, p_uuid, target_stage_id=s_uuid)

        missing_reqs = [f.description for f in audit_run.findings if f.rule_code == "R001"]
        gate_unapproved = [f.description for f in audit_run.findings if f.rule_code == "R002"]
        broken_deps = [f.description for f in audit_run.findings if f.rule_code in ("R003", "R005")]
        ref_violations = [f.description for f in audit_run.findings if f.rule_code == "R007"]
        contradictions = [f.description for f in audit_run.findings if f.rule_code == "R009"]

        return json.dumps(
            {
                "project_id": project_id,
                "readiness_status": audit_run.readiness_status,
                "missing_mandatory_requirements": missing_reqs,
                "unapproved_gate_documents": gate_unapproved,
                "broken_dependencies": broken_deps,
                "permitted_reference_violations": ref_violations,
                "document_contradictions": contradictions,
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"error": str(e)})
    finally:
        db.close()


def query_entity_neighborhood(
    project_id: str, entity_type: str, entity_id: str, depth: int = 1
) -> str:
    """
    Agent Tool: Queries the knowledge graph neighborhood around a specified entity (document, stage, requirement).
    """
    db = SessionLocal()
    try:
        p_uuid = uuid.UUID(project_id)
        e_uuid = uuid.UUID(entity_id)

        center_node = (
            db.query(Node)
            .filter(
                Node.project_id == p_uuid,
                Node.source_id == e_uuid,
            )
            .first()
        )
        if not center_node:
            return json.dumps({"error": f"Entity {entity_type}:{entity_id} not found in knowledge graph"})

        edges = (
            db.query(Edge)
            .filter(
                Edge.project_id == p_uuid,
                (Edge.source_node_id == center_node.node_id) | (Edge.target_node_id == center_node.node_id),
            )
            .all()
        )

        connected_node_ids = {e.source_node_id for e in edges} | {e.target_node_id for e in edges}
        connected_node_ids.discard(center_node.node_id)

        other_nodes = (
            db.query(Node)
            .filter(Node.node_id.in_(connected_node_ids))
            .all()
        )
        node_map = {n.node_id: n for n in other_nodes}

        relationships = []
        for e in edges:
            if e.source_node_id == center_node.node_id:
                target = node_map.get(e.target_node_id)
                if target:
                    relationships.append({
                        "relationship": e.edge_type,
                        "direction": "outgoing",
                        "related_entity": target.label,
                        "related_type": target.entity_type,
                        "properties": e.properties,
                    })
            else:
                source = node_map.get(e.source_node_id)
                if source:
                    relationships.append({
                        "relationship": e.edge_type,
                        "direction": "incoming",
                        "related_entity": source.label,
                        "related_type": source.entity_type,
                        "properties": e.properties,
                    })

        return json.dumps(
            {
                "entity": center_node.label,
                "entity_type": center_node.entity_type,
                "total_connections": len(relationships),
                "connections": relationships,
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"error": str(e)})
    finally:
        db.close()


def query_project_timeline(project_id: str) -> str:
    """
    Agent Tool: Queries recent project progression events and audit runs.
    """
    db = SessionLocal()
    try:
        p_uuid = uuid.UUID(project_id)
        timeline = get_project_timeline(db, p_uuid, limit=20)
        return json.dumps({"project_id": project_id, "timeline": timeline}, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})
    finally:
        db.close()
