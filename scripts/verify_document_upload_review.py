"""
End-to-end verification of the "upload + scan + revise-in-chat + index" flow.

FastAPI TestClient -> real Postgres + real Groq. Covers:
  1) upload a real PDF as carol (contributor) -> pending_review, auto-scan,
     DocumentScan row, chat-interface seed message
  2) revise via chat -> original content preserved + requested change applied
  3) finalize -> NEW DocumentVersion on the SAME document_id, status flips to
     indexed only on a passing/unflagged scan
  4) content-hash skip-rescan: chat-draft a document, finalize it (marker
     embedded), download it, upload that EXACT file -> scan is skipped,
     reusing the embedded score
  5) edit that downloaded file's content -> uploaded again -> scanned fresh
  6) stage dropdown data (GET /projects/{id}/stages) reflects team_stage_access
     — narrows to exactly what's granted, not everything in the project
"""

import hashlib
import io
import uuid

from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

from app.main import app
from app.database import SessionLocal
from app.models.user import User
from app.models.team import Team
from app.models.project import Project
from app.models.stage import Stage, TeamStageAccess
from app.models.document import Document, DocumentVersion, DocumentScan, DocumentTeamVisibility
from app.models.workflow import WorkflowState
from app.models.audit import AuditLog
from app.services.auth import create_session_token
from app.services import draft_workspace

c = TestClient(app)
db = SessionLocal()


def uid(email):
    return db.query(User).filter(User.email == email).one().user_id


def tok(email):
    return {"Authorization": f"Bearer {create_session_token(str(uid(email)))}"}


PA = db.query(Project).filter(Project.name == "Project A").one()
PID = str(PA.project_id)
ENG = db.query(Team).filter(Team.name == "Engineering", Team.project_id == PA.project_id).one()
DESIGN_TEAM = db.query(Team).filter(Team.name == "Design", Team.project_id == PA.project_id).one()


def stage(name):
    return db.query(Stage).filter(
        Stage.project_id == PA.project_id, Stage.name == name, Stage.deleted_at.is_(None)
    ).one()


TESTING = stage("Testing")
DEVELOPMENT = stage("Development")
DESIGN_STAGE = stage("Design")
REQUIREMENTS = stage("Requirements")

FAIL = []
CREATED_DOC_IDS = []


def check(cond, msg):
    print(f"  {'PASS' if cond else 'FAIL'}: {msg}")
    if not cond:
        FAIL.append(msg)


def h(t):
    print("\n" + "=" * 78 + f"\n {t}\n" + "=" * 78)


def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def make_pdf(title: str, sections: list[tuple[str, str]]) -> bytes:
    """A real, Docling-parseable PDF: a title and a few heading/paragraph pairs."""
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=LETTER)
    _, height = LETTER
    y = height - 72
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(72, y, title)
    y -= 36
    for heading, body in sections:
        pdf.setFont("Helvetica-Bold", 13)
        pdf.drawString(72, y, heading)
        y -= 20
        pdf.setFont("Helvetica", 10)
        for line in body.split("\n"):
            pdf.drawString(72, y, line)
            y -= 14
        y -= 14
    pdf.save()
    return buf.getvalue()


def headings(md):
    return sorted(l.strip() for l in md.splitlines() if l.lstrip().startswith("#"))


# ---------------------------------------------------------------------------
h("SETUP — snapshot Engineering's real access, narrow it for this test")
# Reuse the exact snapshot/restore pattern from verify_team_stage_access.py so
# this run doesn't erode scripts/backfill_team_stage_access.py's baseline.
TOUCHED_STAGE_IDS = [TESTING.stage_id, DEVELOPMENT.stage_id, DESIGN_STAGE.stage_id, REQUIREMENTS.stage_id]
baseline_snapshot = [
    (r.team_id, r.stage_id)
    for r in db.query(TeamStageAccess).filter(TeamStageAccess.stage_id.in_(TOUCHED_STAGE_IDS)).all()
]
db.query(TeamStageAccess).filter(TeamStageAccess.stage_id.in_(TOUCHED_STAGE_IDS)).delete(synchronize_session=False)
db.add(TeamStageAccess(team_id=ENG.team_id, stage_id=TESTING.stage_id))
db.add(TeamStageAccess(team_id=ENG.team_id, stage_id=DEVELOPMENT.stage_id))
db.add(TeamStageAccess(team_id=DESIGN_TEAM.team_id, stage_id=DESIGN_STAGE.stage_id))
db.commit()
print("  Engineering narrowed to {Testing, Development}; Design team to {Design}")


# ---------------------------------------------------------------------------
h("1)  carol (contributor, Engineering) uploads a real PDF to Testing")
pdf_bytes = make_pdf("Login Flow Test Plan", [
    ("Scope", "This test plan covers the login flow for the web application."),
    ("Test Cases", "TC1: valid credentials succeed.\nTC2: invalid password is rejected.\nTC3: account locks after 5 failed attempts."),
    ("Environment", "Chrome and Firefox, staging environment, seeded test accounts."),
])
r = c.post(
    "/documents/upload-file", headers=tok("carol@test.com"),
    files={"file": ("login-test-plan.pdf", pdf_bytes, "application/pdf")},
    data={"stage_id": str(TESTING.stage_id), "team_id": str(ENG.team_id), "sensitivity_level": "internal"},
)
check(r.status_code == 201, f"POST /documents/upload-file (PDF) -> {r.status_code}: {r.text[:300] if r.status_code != 201 else ''}")
up1 = r.json()
document_id = uuid.UUID(up1["document_id"]) if up1.get("document_id") else None
if document_id:
    CREATED_DOC_IDS.append(document_id)
check(up1.get("status") == "pending_review", f"upload response status == 'pending_review' (got {up1.get('status')})")
session_id = up1.get("session_id")
check(bool(session_id), "response carries a review session_id")

db.expire_all()
version1 = db.query(DocumentVersion).filter(DocumentVersion.document_id == document_id).one()
check(version1.status.value == "pending_review", f"DocumentVersion(v1).status == pending_review in DB (got {version1.status.value})")
check(version1.version_number == 1, "this is version 1")
check(version1.file_data == pdf_bytes, "DocumentVersion.file_data == the RAW uploaded PDF bytes, untouched")

doc_scan1 = db.query(DocumentScan).filter(DocumentScan.version_id == version1.version_id).one_or_none()
check(doc_scan1 is not None, "a DocumentScan row was created for version 1 (auto-scan)")
if doc_scan1:
    print(f"  auto-scan: {doc_scan1.overall_score}/60, injection_flagged={doc_scan1.injection_flagged}")
check(up1.get("scan") is not None or up1.get("scan_error"), "upload response carries a scan result or a scan_error")
check("Structure Scanner" in (up1.get("reply") or "") or "score" in (up1.get("reply") or "").lower(),
      "chat-interface seed reply mentions the Scanner result")
print(f"  seed reply: {up1.get('reply', '')[:200]}")

before_content = draft_workspace.read_working_draft(session_id)
check(before_content is not None and "Login Flow Test Plan" in before_content,
      "review session's working file was seeded with the PARSED PDF content (Docling)")
print(f"  parsed content ({len(before_content or '')} chars): {(before_content or '')[:150]!r}")


# ---------------------------------------------------------------------------
h("2)  Revise via chat — original content preserved, requested change applied")
r2 = c.post("/documents/review/message", headers=tok("carol@test.com"), json={
    "document_id": str(document_id), "session_id": session_id,
    "message": "Add a new section covering browser cookie/session expiry during login. Keep everything else exactly as it is.",
})
check(r2.status_code == 200, f"POST /documents/review/message (revise) -> {r2.status_code}: {r2.text[:300] if r2.status_code != 200 else ''}")
rev = r2.json()
check(rev.get("drafted") is True and rev.get("finalized") is False, "response: drafted=true, finalized=false")
after_content = draft_workspace.read_working_draft(session_id)
check(after_content is not None and after_content != before_content, "working file changed after the revision")
kept = [hd for hd in headings(before_content or "") if hd in headings(after_content or "")]
check(len(kept) == len(headings(before_content or "")),
      f"all {len(headings(before_content or ''))} original section headings survive ({len(kept)} kept)")
check("expir" in (after_content or "").lower() or "cookie" in (after_content or "").lower() or "session" in (after_content or "").lower(),
      "the requested cookie/session-expiry content was added")
final_expected = after_content
final_expected_hash = sha(final_expected)


# ---------------------------------------------------------------------------
h("3)  Finalize — NEW DocumentVersion on the SAME document_id")
r3 = c.post("/documents/review/message", headers=tok("carol@test.com"), json={
    "document_id": str(document_id), "session_id": session_id, "message": "That looks good, finalize it.",
})
check(r3.status_code == 200, f"POST /documents/review/message (finalize) -> {r3.status_code}: {r3.text[:300] if r3.status_code != 200 else ''}")
fin = r3.json()
check(fin.get("finalized") is True, "response: finalized=true")
check(fin.get("version_number") == 2, f"new version_number == 2 (got {fin.get('version_number')})")
check(fin.get("status") in ("indexed", "pending_review"), f"status is a real DocumentVersion status (got {fin.get('status')})")
print(f"  finalize: version {fin.get('version_number')}, status={fin.get('status')}, "
      f"score={fin.get('scan', {}).get('overall_score') if fin.get('scan') else None}")

db.expire_all()
versions = db.query(DocumentVersion).filter(DocumentVersion.document_id == document_id).order_by(DocumentVersion.version_number).all()
check(len(versions) == 2, f"exactly 2 DocumentVersion rows now exist for this document_id (got {len(versions)})")
check(all(v.document_id == document_id for v in versions), "both versions belong to the SAME document_id (no new Document created)")
v2 = versions[-1]
check(v2.file_data.decode("utf-8") == final_expected, "v2.file_data == the exact chat-revised content")
check(v2.status.value == fin.get("status"), "v2.status in DB matches the finalize response")

document_row = db.get(Document, document_id)
check(document_row.current_version_id == v2.version_id, "Document.current_version_id now points at v2")
check(document_row.stage_id == TESTING.stage_id, "the document's stage_id is unchanged (single-stage-per-upload)")

doc_scan2 = db.query(DocumentScan).filter(DocumentScan.version_id == v2.version_id).one_or_none()
check(doc_scan2 is not None, "a DocumentScan row was created for version 2 (finalize scan)")

if fin.get("status") == "indexed":
    check(doc_scan2 is not None and doc_scan2.overall_score >= 36 and not doc_scan2.injection_flagged,
          "status == indexed implies a passing, unflagged scan (as designed)")
else:
    print("  (finalize scan did not pass the threshold this run — status correctly stayed pending_review, not a bug)")


# ---------------------------------------------------------------------------
h("4)  Content-hash skip-rescan — chat-draft, finalize, download, re-upload UNCHANGED")
draft_session = "verify-upload-review-draft"
wip_path = draft_workspace._wip_path(draft_session)
if wip_path.exists():
    wip_path.unlink()

rd1 = c.post("/agents/draft/message", headers=tok("carol@test.com"), json={
    "session_id": draft_session,
    "message": "Draft a one-paragraph note describing our API rate-limiting policy: token bucket, 429 on overflow.",
})
check(rd1.status_code == 200, f"chat-draft turn -> {rd1.status_code}")
rd2 = c.post("/agents/draft/message", headers=tok("carol@test.com"), json={
    "session_id": draft_session, "message": "That's good, finalize it.",
})
check(rd2.status_code == 200 and rd2.json().get("finalized"), "chat-draft finalized")
draft_final = rd2.json()
marker = draft_workspace.check_scan_marker(draft_final["final_content"])
check(marker is not None, "the finalized chat-draft carries a valid docflow-scan marker")
original_score = marker["score"] if marker else None
print(f"  chat-drafted file finalized with embedded score {original_score}/60")

dl = c.get(draft_final["download_url"], headers=tok("carol@test.com"))
check(dl.status_code == 200, "downloaded the finalized chat-draft file")
downloaded_bytes = dl.content
check(downloaded_bytes.decode("utf-8") == draft_final["final_content"], "downloaded bytes == final_content (exact)")

r4 = c.post(
    "/documents/upload-file", headers=tok("carol@test.com"),
    files={"file": ("rate-limit-note.md", downloaded_bytes, "text/markdown")},
    data={"stage_id": str(DEVELOPMENT.stage_id), "team_id": str(ENG.team_id), "sensitivity_level": "internal"},
)
check(r4.status_code == 201, f"re-upload the EXACT downloaded file -> {r4.status_code}: {r4.text[:300] if r4.status_code != 201 else ''}")
up4 = r4.json()
if up4.get("document_id"):
    CREATED_DOC_IDS.append(uuid.UUID(up4["document_id"]))
check(up4.get("scan_skipped") is True, "scan_skipped == True — the content-hash marker matched, structural rescan SKIPPED")
check(up4.get("scan", {}).get("overall_score") == original_score,
      f"reused score ({up4.get('scan', {}).get('overall_score')}) == original chat-draft score ({original_score})")
check(up4.get("status") == "pending_review", "still starts pending_review even with a reused score (finalize is still required)")


# ---------------------------------------------------------------------------
h("5)  Edit that downloaded file, then re-upload — scanned FRESH, not skipped")
edited_content = downloaded_bytes.decode("utf-8") + "\n\nAn additional paragraph was added after download, changing the content.\n"
edited_bytes = edited_content.encode("utf-8")
check(edited_bytes != downloaded_bytes, "sanity: edited bytes differ from the original download")

r5 = c.post(
    "/documents/upload-file", headers=tok("carol@test.com"),
    files={"file": ("rate-limit-note-edited.md", edited_bytes, "text/markdown")},
    data={"stage_id": str(DEVELOPMENT.stage_id), "team_id": str(ENG.team_id), "sensitivity_level": "internal"},
)
check(r5.status_code == 201, f"upload the EDITED file -> {r5.status_code}: {r5.text[:300] if r5.status_code != 201 else ''}")
up5 = r5.json()
if up5.get("document_id"):
    CREATED_DOC_IDS.append(uuid.UUID(up5["document_id"]))
check(up5.get("scan_skipped") is False, "scan_skipped == False — hash no longer matches (content was edited), scanned fresh")


# ---------------------------------------------------------------------------
h("6)  Stage dropdown data reflects team_stage_access — narrowed, not everything")
r6 = c.get(f"/projects/{PID}/stages", headers=tok("carol@test.com"))
check(r6.status_code == 200, "GET /projects/{id}/stages as carol -> 200")
carol_stage_names = {s["name"] for s in r6.json()}
check(carol_stage_names == {"Testing", "Development"},
      f"carol (Engineering only, narrowed to Testing+Development for this test) sees exactly that, got {carol_stage_names}")
# Design/Requirements were NOT granted to Engineering in this test's setup —
# confirm carol genuinely cannot see or upload there.
check("Design" not in carol_stage_names and "Requirements" not in carol_stage_names,
      "Design/Requirements (not granted to Engineering) are absent from carol's stage list")
r6b = c.post(
    "/documents/upload-file", headers=tok("carol@test.com"),
    files={"file": ("x.md", b"# X\ncontent", "text/markdown")},
    data={"stage_id": str(DESIGN_STAGE.stage_id), "team_id": str(ENG.team_id), "sensitivity_level": "internal"},
)
check(r6b.status_code == 403, f"carol upload to Design (not granted) -> {r6b.status_code} (should be 403)")


# ---------------------------------------------------------------------------
h("CLEANUP")
# Restore the real team_stage_access baseline this script displaced.
db.query(TeamStageAccess).filter(TeamStageAccess.stage_id.in_(TOUCHED_STAGE_IDS)).delete(synchronize_session=False)
for team_id, stage_id in baseline_snapshot:
    db.add(TeamStageAccess(team_id=team_id, stage_id=stage_id))
db.commit()
print(f"  restored {len(baseline_snapshot)} pre-test team_stage_access row(s)")

for doc_id in CREATED_DOC_IDS:
    db.query(Document).filter(Document.document_id == doc_id).update(
        {"current_version_id": None}, synchronize_session=False
    )
    db.query(DocumentScan).filter(
        DocumentScan.version_id.in_(
            db.query(DocumentVersion.version_id).filter(DocumentVersion.document_id == doc_id)
        )
    ).delete(synchronize_session=False)
    db.query(WorkflowState).filter(WorkflowState.document_id == doc_id).delete(synchronize_session=False)
    db.query(DocumentVersion).filter(DocumentVersion.document_id == doc_id).delete(synchronize_session=False)
    db.query(DocumentTeamVisibility).filter(DocumentTeamVisibility.document_id == doc_id).delete(synchronize_session=False)
    db.query(Document).filter(Document.document_id == doc_id).delete(synchronize_session=False)
if CREATED_DOC_IDS:
    db.query(AuditLog).filter(
        AuditLog.action.in_(["UPLOAD_DOCUMENT", "FINALIZE_DOCUMENT_REVISION"]),
        AuditLog.resource_id.in_(CREATED_DOC_IDS),
    ).delete(synchronize_session=False)
db.commit()

for sid in (session_id, draft_session):
    p = draft_workspace._wip_path(sid) if sid else None
    if p and p.exists():
        p.unlink()
from app.services.draft_export import DRAFTS_DIR
if draft_final.get("path"):
    from pathlib import Path
    fp = Path(draft_final["path"])
    if fp.exists() and fp.is_relative_to(DRAFTS_DIR):
        fp.unlink()

print(f"  removed {len(CREATED_DOC_IDS)} test document(s), their versions/scans, and the saved chat-draft file")

h("RESULT")
print(f"  {'ALL CHECKS PASSED' if not FAIL else 'FAILURES: ' + '; '.join(FAIL)}")
db.close()

if FAIL:
    raise SystemExit(1)
