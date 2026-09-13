import re
import uuid
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session

from app.models.graph import Claim, Edge, Node
from app.models.document import Document, DocumentVersion
from app.services.graph.audit_rules import FindingSpec


# Extraction patterns for explicit semantic statements / claims
CLAIM_PATTERNS = [
    # 1. Dates / Deadlines / Timelines
    {
        "type": "timeline",
        "subject": "launch date",
        "predicate": "scheduled_for",
        "regex": re.compile(r"(?:launch|delivery|release|go-live|completion)\s+(?:date\s+)?(?:is\s+|set\s+to\s+|:\s*)([A-Za-z0-9\s,\-\/]+?)(?:\.|\n|$)", re.IGNORECASE),
    },
    {
        "type": "timeline",
        "subject": "project deadline",
        "predicate": "scheduled_for",
        "regex": re.compile(r"(?:project\s+deadline|target\s+completion)\s+(?:is\s+|:\s*)([A-Za-z0-9\s,\-\/]+?)(?:\.|\n|$)", re.IGNORECASE),
    },
    # 2. Budgets / Financials
    {
        "type": "financial",
        "subject": "project budget",
        "predicate": "allocated_amount",
        "regex": re.compile(r"(?:total\s+)?(?:project\s+)?(?:budget|cost|funding|allocation)\s+(?:is\s+|of\s+|:\s*)(\$?\s*[0-9,]+(?:\.[0-9]+)?\s*(?:USD|k|million|M|k|K|billion|B)?)(?:\.|\n|$)", re.IGNORECASE),
    },
    # 3. Architecture / Technology direct choices
    {
        "type": "architecture",
        "subject": "primary database",
        "predicate": "technology_choice",
        "regex": re.compile(r"(?:primary\s+)?(?:database|datastore|backend\s+db)\s+(?:is\s+|selected\s+is\s+|:\s*)([A-Za-z0-9_\-]+)(?:\.|\n|$)", re.IGNORECASE),
    },
    {
        "type": "architecture",
        "subject": "cloud provider",
        "predicate": "technology_choice",
        "regex": re.compile(r"(?:cloud\s+provider|hosting\s+platform)\s+(?:is\s+|:\s*)([A-Za-z0-9_\-]+)(?:\.|\n|$)", re.IGNORECASE),
    },
    # 4. Security & Compliance directives
    {
        "type": "security",
        "subject": "encryption at rest",
        "predicate": "requirement_status",
        "regex": re.compile(r"(?:encryption\s+at\s+rest|storage\s+encryption)\s+(?:is\s+|:\s*)(mandatory|required|optional|not\s+required|prohibited|AES-[0-9]+)(?:\.|\n|$)", re.IGNORECASE),
    },
    {
        "type": "security",
        "subject": "two-factor authentication",
        "predicate": "requirement_status",
        "regex": re.compile(r"(?:two-factor\s+authentication|2fa|mfa)\s+(?:is\s+|:\s*)(mandatory|required|optional|disabled|not\s+required)(?:\.|\n|$)", re.IGNORECASE),
    },
]


def extract_claims_from_text(
    text: str,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    version_id: uuid.UUID,
) -> List[Dict[str, Any]]:
    """
    Extracts structured factual claims from raw text using pattern recognition.
    """
    claims_data: List[Dict[str, Any]] = []
    if not text:
        return claims_data

    for pattern in CLAIM_PATTERNS:
        matches = pattern["regex"].finditer(text)
        for match in matches:
            val = match.group(1).strip()
            # Basic polarity detection
            polarity = True
            if val.lower() in ("optional", "not required", "disabled", "prohibited", "false"):
                polarity = False

            snippet = match.group(0).strip()
            claims_data.append({
                "tenant_id": tenant_id,
                "project_id": project_id,
                "document_id": document_id,
                "version_id": version_id,
                "subject": pattern["subject"],
                "predicate": pattern["predicate"],
                "object": val,
                "polarity": polarity,
                "snippet": snippet,
                "source_locator": {
                    "start": match.start(),
                    "end": match.end(),
                    "claim_type": pattern["type"],
                },
                "confidence": 0.95,
            })

    return claims_data


def persist_claims(
    db: Session,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    claims_data: List[Dict[str, Any]],
    extraction_run_id: Optional[uuid.UUID] = None,
) -> List[Claim]:
    """
    Persists extracted claims into knowledge.claims idempotently.
    Overwrites previous claims for the same document version.
    """
    db.query(Claim).filter(Claim.version_id == version_id).delete()

    created_claims: List[Claim] = []
    for cd in claims_data:
        claim = Claim(
            tenant_id=tenant_id,
            project_id=project_id,
            document_id=document_id,
            version_id=version_id,
            subject=cd["subject"],
            predicate=cd["predicate"],
            object=cd["object"],
            polarity=cd.get("polarity", True),
            snippet=cd.get("snippet", ""),
            source_locator=cd.get("source_locator", {}),
            confidence=cd.get("confidence", 1.0),
            extraction_run_id=extraction_run_id,
        )
        db.add(claim)
        created_claims.append(claim)

    db.flush()
    return created_claims


def detect_project_contradictions(
    db: Session,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    evaluated_stage_ids: Optional[List[uuid.UUID]] = None,
) -> List[FindingSpec]:
    """
    Detects contradictions between claims across documents in scope.
    Generates CONFLICTS_WITH edges between conflicting document nodes and
    returns R009 blocker findings.
    """
    findings: List[FindingSpec] = []

    # Query claims joined with Document to check stage scope
    query = (
        db.query(Claim, Document)
        .join(Document, Claim.document_id == Document.document_id)
        .filter(Claim.tenant_id == tenant_id, Claim.project_id == project_id)
    )

    if evaluated_stage_ids is not None:
        query = query.filter(Document.stage_id.in_(evaluated_stage_ids))

    results = query.all()
    if not results:
        return findings

    # Group claims by (subject.lower(), predicate.lower())
    groups: Dict[Tuple[str, str], List[Tuple[Claim, Document]]] = {}
    for claim, doc in results:
        key = (claim.subject.lower().strip(), claim.predicate.lower().strip())
        groups.setdefault(key, []).append((claim, doc))

    # Pre-fetch document graph nodes
    doc_ids = list({doc.document_id for _, doc in results})
    doc_nodes = (
        db.query(Node)
        .filter(
            Node.tenant_id == tenant_id,
            Node.project_id == project_id,
            Node.source_table == "documents",
            Node.source_id.in_(doc_ids),
        )
        .all()
    )
    node_map = {n.source_id: n for n in doc_nodes}

    seen_pairs = set()

    for (subj, pred), claim_tuples in groups.items():
        if len(claim_tuples) < 2:
            continue

        for i in range(len(claim_tuples)):
            for j in range(i + 1, len(claim_tuples)):
                c1, doc1 = claim_tuples[i]
                c2, doc2 = claim_tuples[j]

                # If same document, don't flag cross-document contradiction
                if doc1.document_id == doc2.document_id:
                    continue

                pair_key = tuple(sorted([str(c1.claim_id), str(c2.claim_id)]))
                if pair_key in seen_pairs:
                    continue

                # Contradiction criteria:
                # 1. Different polarities (e.g. mandatory vs optional)
                # 2. Different values for same polarity
                val1_clean = c1.object.lower().strip().replace("$", "").replace(",", "")
                val2_clean = c2.object.lower().strip().replace("$", "").replace(",", "")

                is_contradiction = False
                if c1.polarity != c2.polarity:
                    is_contradiction = True
                elif val1_clean != val2_clean:
                    is_contradiction = True

                if is_contradiction:
                    seen_pairs.add(pair_key)

                    # Upsert CONFLICTS_WITH edge in graph
                    node1 = node_map.get(doc1.document_id)
                    node2 = node_map.get(doc2.document_id)
                    if node1 and node2:
                        existing_edge = (
                            db.query(Edge)
                            .filter(
                                Edge.source_node_id == node1.node_id,
                                Edge.target_node_id == node2.node_id,
                                Edge.edge_type == "CONFLICTS_WITH",
                            )
                            .first()
                        )
                        conflict_props = {
                            "claim1_id": str(c1.claim_id),
                            "claim2_id": str(c2.claim_id),
                            "subject": c1.subject,
                            "predicate": c1.predicate,
                            "value1": c1.object,
                            "value2": c2.object,
                            "snippet1": c1.snippet,
                            "snippet2": c2.snippet,
                        }
                        if existing_edge:
                            existing_edge.properties = conflict_props
                        else:
                            db.add(
                                Edge(
                                    tenant_id=tenant_id,
                                    project_id=project_id,
                                    source_node_id=node1.node_id,
                                    target_node_id=node2.node_id,
                                    edge_type="CONFLICTS_WITH",
                                    confidence=1.0,
                                    properties=conflict_props,
                                )
                            )
                        db.flush()

                    # Emit R009 blocker finding
                    findings.append(
                        FindingSpec(
                            rule_code="R009",
                            severity="HIGH",
                            is_blocker=True,
                            title="Document Contradiction Detected",
                            description=(
                                f"Direct contradiction detected between '{doc1.original_filename}' and '{doc2.original_filename}' "
                                f"regarding '{c1.subject}': '{c1.object}' vs '{c2.object}'."
                            ),
                            affected_entity_type="document",
                            affected_entity_id=doc1.document_id,
                            target_stage_id=doc1.stage_id,
                            evidence_sources=[
                                {
                                    "document_id": str(doc1.document_id),
                                    "version_id": str(c1.version_id),
                                    "snippet": c1.snippet,
                                },
                                {
                                    "document_id": str(doc2.document_id),
                                    "version_id": str(c2.version_id),
                                    "snippet": c2.snippet,
                                },
                            ],
                            details={
                                "conflicting_document_id": str(doc2.document_id),
                                "conflicting_document_name": doc2.original_filename,
                                "subject": c1.subject,
                                "predicate": c1.predicate,
                                "value_a": c1.object,
                                "value_b": c2.object,
                                "snippet_a": c1.snippet,
                                "snippet_b": c2.snippet,
                            },
                        )
                    )

    return findings
