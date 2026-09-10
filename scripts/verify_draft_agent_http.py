"""
End-to-end verification of the Drafting/Scanner agents over HTTP
(POST /agents/draft/message, GET /agents/draft/download/{filename}).

FastAPI TestClient -> real Groq. Covers: draft, revision (content
preservation), finalize (Scanner score), download-matches-final (SHA-256),
two concurrent sessions not interfering, auth, path-traversal guards.
"""

import hashlib
import re

from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models.user import User
from app.services import draft_workspace
from app.services.auth import create_session_token

c = TestClient(app)
db = SessionLocal()
BOB = {"Authorization": f"Bearer {create_session_token(str(db.query(User).filter(User.email=='bob@test.com').one().user_id))}"}
db.close()

FAIL = []
def check(cond, msg):
    print(f"  {'PASS' if cond else 'FAIL'}: {msg}")
    if not cond:
        FAIL.append(msg)

def h(t):
    print("\n" + "=" * 78 + f"\n {t}\n" + "=" * 78)

def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def headings(md):
    return sorted(l.strip() for l in md.splitlines() if l.lstrip().startswith("#"))

def msg(session_id, message):
    r = c.post("/agents/draft/message", headers=BOB,
               json={"session_id": session_id, "message": message})
    assert r.status_code == 200, f"{r.status_code}: {r.text[:300]}"
    return r.json()

def wipe(sid):
    p = draft_workspace._wip_path(sid)
    if p.exists():
        p.unlink()


# ---------------------------------------------------------------------------
h("1)  DRAFT  (session A)")
A = "verify-draft-A"
wipe(A)
r1 = msg(A, "Draft a concise test plan for a login page. Cover the happy path, "
            "an invalid password, and account lockout after repeated failures.")
check(r1["drafted"] is True and r1["finalized"] is False, "response: drafted=true, finalized=false")
wip1 = draft_workspace.read_working_draft(A)
check(bool(wip1) and wip1.lstrip().startswith("#"), "working file drafts/.wip/<A>.md exists and is Markdown")
print(f"  working file: {len(wip1)} chars, headings: {headings(wip1)}")
v1_headings = headings(wip1)
v1_hash = sha(wip1)


# ---------------------------------------------------------------------------
h("2)  REVISION  (session A)  — full content preserved")
r2 = msg(A, "Add a new section covering the 'forgot password' reset flow. "
            "Keep everything else exactly as it is.")
check(r2["drafted"] is True and r2["finalized"] is False, "response: drafted=true")
wip2 = draft_workspace.read_working_draft(A)
check(sha(wip2) != v1_hash, "working file changed after the revision")
kept = [hd for hd in v1_headings if hd in headings(wip2)]
check(len(kept) == len(v1_headings),
      f"all {len(v1_headings)} original section headings survive the revision ({len(kept)} kept)")
added = [hd for hd in headings(wip2) if hd not in v1_headings]
check(any("reset" in hd.lower() or "forgot" in hd.lower() or "password" in hd.lower() for hd in added)
      or "reset" in wip2.lower(),
      "the requested 'forgot password' content was added")
print(f"  v1 headings: {v1_headings}")
print(f"  v2 headings: {headings(wip2)}")
final_expected = wip2
final_expected_hash = sha(final_expected)


# ---------------------------------------------------------------------------
h("3)  FINALIZE  (session A)  — Scanner score + download url")
r3 = msg(A, "That looks good, finalize it.")
check(r3["finalized"] is True, "response: finalized=true")
check(isinstance(r3.get("scan"), dict) and "overall_score" in r3["scan"],
      f"Scanner score present: {r3['scan']['overall_score'] if r3.get('scan') else None}/60")
if r3.get("scan"):
    print(f"  score {r3['scan']['overall_score']}/60 — {[ (cc['name'], cc['score']) for cc in r3['scan']['criteria'] ]}")
    print(f"  summary: {r3['scan']['summary'][:160]}")
check(bool(r3.get("download_url")) and r3["download_url"].startswith("/agents/draft/download/"),
      f"download_url returned: {r3.get('download_url')}")
check(not draft_workspace.has_working_draft(A), "working file deleted after finalize")

# Phase A gap-closer §8: finalize now prepends a hidden docflow-scan marker
# (score + a SHA-256 of the content below it) so a later re-upload of this
# exact file can skip re-scanning — final_content carries it, not the raw wip2.
marker = draft_workspace.check_scan_marker(r3["final_content"])
check(marker is not None, "finalize response 'final_content' carries a valid docflow-scan marker")
if marker:
    check(marker["score"] == r3["scan"]["overall_score"], "marker's embedded score matches the scan result")
    check(sha(marker["content_without_marker"]) == final_expected_hash,
          "content below the marker == the working file's last revision (SHA-256 match)")


# ---------------------------------------------------------------------------
h("4)  DOWNLOAD  — bytes match the finalized draft exactly")
dl = c.get(r3["download_url"], headers=BOB)
check(dl.status_code == 200, f"GET {r3['download_url']} -> 200")
check(dl.headers.get("content-type", "").startswith("text/markdown"), "served as text/markdown")
downloaded = dl.text
print(f"  downloaded {len(downloaded)} chars   sha256={sha(downloaded)[:16]}…")
print(f"  final_content   {len(r3['final_content'])} chars   sha256={sha(r3['final_content'])[:16]}…")
check(sha(downloaded) == sha(r3["final_content"]),
      "downloaded file bytes == final_content shown in the finalize response (SHA-256 match, marker included)")


# ---------------------------------------------------------------------------
h("5)  CONCURRENCY  — two sessions interleaved, no cross-talk")
B, C = "verify-draft-B", "verify-draft-C"
wipe(B); wipe(C)
msg(B, "Draft a one-paragraph API rate-limiting design note. Mention token bucket and 429 responses.")
msg(C, "Draft a short onboarding checklist for a new backend engineer. Laptop, repo access, on-call handbook.")
msg(B, "Add a sentence about per-tenant limits. Keep the rest unchanged.")
wb = draft_workspace.read_working_draft(B)
wc = draft_workspace.read_working_draft(C)
check("bucket" in wb.lower() or "429" in wb or "rate" in wb.lower(), "session B working file is the rate-limiting note")
check("onboard" in wc.lower() or "checklist" in wc.lower() or "laptop" in wc.lower(), "session C working file is the onboarding checklist")
check("onboard" not in wb.lower() and "laptop" not in wb.lower(), "session B was NOT polluted by session C")
check("429" not in wc and "token bucket" not in wc.lower(), "session C was NOT polluted by session B")
fb = msg(B, "finalize it")
fc = msg(C, "finalize please")
check(fb["finalized"] and fc["finalized"], "both B and C finalize independently")
check(fb["download_url"] != fc["download_url"], "B and C produced different files")
db_txt = c.get(fb["download_url"], headers=BOB).text
dc_txt = c.get(fc["download_url"], headers=BOB).text
check(sha(db_txt) == sha(fb["final_content"]) and sha(dc_txt) == sha(fc["final_content"]),
      "each downloaded file matches its own session's finalized content")
check("laptop" not in db_txt.lower() and "429" not in dc_txt, "final files are not cross-contaminated")


# ---------------------------------------------------------------------------
h("6)  AUTH + INPUT GUARDS")
check(c.post("/agents/draft/message", json={"session_id": "x", "message": "hi"}).status_code == 401,
      "POST /agents/draft/message without a token -> 401")
check(c.get("/agents/draft/download/anything.md").status_code == 401,
      "GET /agents/draft/download without a token -> 401")
check(c.post("/agents/draft/message", headers=BOB,
             json={"session_id": "../escape", "message": "hi"}).status_code == 422,
      "session_id containing '../' -> 422")
check(c.get("/agents/draft/download/..%2f..%2fsecret.md", headers=BOB).status_code in (404, 422),
      "download filename traversal attempt -> 404/422")
check(c.get("/agents/draft/download/does-not-exist-xyz.md", headers=BOB).status_code == 404,
      "download of a missing file -> 404")


h("RESULT")
print(f"  {'ALL CHECKS PASSED' if not FAIL else 'FAILURES: ' + '; '.join(FAIL)}")
