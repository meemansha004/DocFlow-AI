"""
Calibration check for app/services/rag/reranking.RELEVANCE_FLOOR.

The floor was 0.0 ("the model's zero-crossing"). That was wrong: the MS MARCO
cross-encoder produces raw ranking logits with NO calibrated zero-crossing,
and 0.0 was silently discarding a large fraction of genuinely relevant
chunks — anything phrased as a natural question rather than a keyword search.

This script measures the actual score distribution for the model over a
labelled set of query/chunk pairs (keyword / natural / meta / rambling
phrasings, plus hard negatives and semantic near-misses) and shows:

  * where relevant / unrelated / near-miss pairs actually land,
  * how many relevant chunks the OLD floor (0.0) drops,
  * that the NEW floor keeps them while still rejecting every hard negative.

Near-misses (e.g. "parental leave policy" vs a vacation chunk) are shown for
information — they can pass the floor, and that is by design: the floor is a
noise gate, and generation is prompted to decline when the chunks don't
actually answer the question.

Pure cross-encoder — no Groq, no DB, no Qdrant. Deterministic.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from app.services.rag.reranking import RELEVANCE_FLOOR, rerank_scores

OLD_FLOOR = 0.0

CHUNKS = {
    "remote": (
        "Full-time employees who have completed their 90-day probation period may work "
        "remotely up to three days per week. Mondays are a required in-office day for everyone "
        "so that all-hands meetings can be held in person. Contractors are not covered by this "
        "remote-work policy and are expected on-site every working day. Managers may approve "
        "additional remote days on a case-by-case basis for medical or caregiving reasons."
    ),
    "vacation": (
        "Full-time employees accrue 18 vacation days per year, available once the 90-day "
        "probation period is complete. Vacation is accrued monthly at 1.5 days per month. "
        "Unused vacation days roll over into the next calendar year up to a cap of five days; "
        "anything above the cap is forfeited on 1 January. Vacation requests should be "
        "submitted at least two weeks in advance through the HR portal."
    ),
    "equipment": (
        "The company provides each employee with a laptop and one external monitor for home "
        "office use. Standard peripherals (keyboard, mouse, headset) are included on request. "
        "Additional or upgraded equipment requests go through the IT portal and require "
        "manager approval. Equipment must be returned within 10 business days of leaving."
    ),
    "incident": (
        "When a production incident is detected, the on-call engineer must acknowledge the "
        "page within 15 minutes and open a bridge call. The incident commander role is assumed "
        "by the on-call lead. A written postmortem, including a timeline and remediation "
        "items, is required within 48 hours of resolution for any SEV1 or SEV2 incident."
    ),
    "expenses": (
        "Travel and business expenses must be submitted within 30 days of being incurred, "
        "with itemized receipts attached for any line item over 25 dollars. Meals while "
        "travelling are reimbursed up to a daily per-diem rate set by the finance team."
    ),
    "onboarding": (
        "New hires complete IT setup and account provisioning on day one, meet their "
        "onboarding buddy, and review the security-awareness module during their first week. "
        "Benefits enrolment must be completed within 30 days of the start date."
    ),
}

# (query, chunk_key, relevant, phrasing_style)
CASES = [
    ("remote work policy", "remote", True, "keyword"),
    ("work from home rules", "remote", True, "keyword"),
    ("How many days a week can employees work remotely?", "remote", True, "natural"),
    ("Can full-time staff work from home?", "remote", True, "natural"),
    ("Are contractors allowed to work remotely?", "remote", True, "natural"),
    ("What does the handbook say about remote work?", "remote", True, "meta"),
    ("What does the company policy say about working from home?", "remote", True, "meta"),
    ("so is there any kind of policy around wfh, like are we allowed to do it and how often",
     "remote", True, "rambling"),
    ("vacation days per year", "vacation", True, "keyword"),
    ("paid time off allowance", "vacation", True, "keyword"),
    ("when do I lose unused vacation", "vacation", True, "keyword"),
    ("How much vacation time do employees get?", "vacation", True, "natural"),
    ("Do unused vacation days roll over?", "vacation", True, "natural"),
    ("What is the company's vacation policy?", "vacation", True, "meta"),
    ("What does the handbook say about paid leave?", "vacation", True, "meta"),
    ("I'm trying to figure out my time off — how many vacation days do I get and do they carry over",
     "vacation", True, "rambling"),
    ("home office equipment", "equipment", True, "keyword"),
    ("What equipment does the company provide for remote workers?", "equipment", True, "natural"),
    ("Can I get a second monitor for working from home?", "equipment", True, "natural"),
    ("What does the handbook say about laptops?", "equipment", True, "meta"),
    ("incident response process", "incident", True, "keyword"),
    ("How quickly must on-call acknowledge a page?", "incident", True, "natural"),
    ("What is the postmortem deadline after an incident?", "incident", True, "natural"),
    ("expense reimbursement deadline", "expenses", True, "keyword"),
    ("How do I submit travel receipts?", "expenses", True, "natural"),
    ("What is the per diem for meals while travelling?", "expenses", True, "natural"),
    # hard negatives — query on topic X vs chunk on unrelated topic Y
    ("remote work policy", "incident", False, "neg"),
    ("remote work policy", "expenses", False, "neg"),
    ("remote work policy", "vacation", False, "neg"),
    ("How many days a week can employees work remotely?", "incident", False, "neg"),
    ("What is the company's vacation policy?", "equipment", False, "neg"),
    ("How much vacation time do employees get?", "incident", False, "neg"),
    ("home office equipment", "vacation", False, "neg"),
    ("home office equipment", "incident", False, "neg"),
    ("incident response process", "remote", False, "neg"),
    ("What is the postmortem deadline?", "vacation", False, "neg"),
    ("expense reimbursement deadline", "vacation", False, "neg"),
    ("How do I submit travel receipts?", "remote", False, "neg"),
    ("how do I file a bug report", "remote", False, "neg"),
    ("parking policy", "vacation", False, "neg"),
    ("What does the handbook say about stock options?", "remote", False, "neg"),
    ("dental insurance coverage", "onboarding", False, "neg"),
    ("401k matching", "expenses", False, "neg"),
    ("I'm trying to figure out my vacation days and whether they roll over", "incident", False, "neg"),
]

# Semantic near-misses — shown for information, NOT asserted as must-reject.
# The honest answer to these is "we have <adjacent policy> but nothing
# specifically on <what you asked>", which generation produces from the chunk.
NEAR_MISSES = [
    ("What is the company's parental leave policy?", "vacation"),
    ("Is there a sabbatical policy?", "vacation"),
    ("What's the reimbursement for a home internet upgrade?", "equipment"),
]

FAIL = []


def check(cond, msg):
    print(f"  {'PASS' if cond else 'FAIL'}: {msg}")
    if not cond:
        FAIL.append(msg)


scored = []
for q, ck, rel, style in CASES:
    s = rerank_scores(q, [CHUNKS[ck]])[0]
    scored.append((s, rel, style, q, ck))

rel = [s for s, r, *_ in scored if r]
neg = [s for s, r, *_ in scored if not r]

print("=" * 92)
print("Cross-encoder score for every labelled pair (sorted)")
print("=" * 92)
print(f"{'score':>8}  {'label':<4} {'style':>9}  query  ->  chunk")
for s, r, style, q, ck in sorted(scored):
    tag = ""
    if r and s <= OLD_FLOOR:
        tag = "   << relevant, DROPPED by old floor 0.0"
    if r and OLD_FLOOR >= s > RELEVANCE_FLOOR:
        tag += "  (kept by new floor)"
    if (not r) and s > RELEVANCE_FLOOR:
        tag = "   << FALSE POSITIVE at new floor"
    print(f"{s:8.2f}  {('REL' if r else 'neg'):<4} {style:>9}  {q[:58]}  ->  {ck}{tag}")

print()
print(f"  RELEVANT pairs   (n={len(rel)}): min {min(rel):+.2f} | median {sorted(rel)[len(rel)//2]:+.2f} | max {max(rel):+.2f}")
print(f"  IRRELEVANT pairs (n={len(neg)}): min {min(neg):+.2f} | median {sorted(neg)[len(neg)//2]:+.2f} | max {max(neg):+.2f}  <- ceiling for a true negative")
print(f"  empty band between them: ({max(neg):+.2f} .. {min(rel):+.2f})")

print()
print("  semantic near-misses (informational — may pass the floor; generation is the backstop):")
for q, ck in NEAR_MISSES:
    s = rerank_scores(q, [CHUNKS[ck]])[0]
    print(f"    {s:8.2f}  {'passes' if s > RELEVANCE_FLOOR else 'floored'}   {q}  ->  {ck}")

print()
print("=" * 92)
print(f"{'floor':>7} | {'REL kept':>8} {'REL dropped':>11} | {'FP (neg kept)':>13} {'neg rejected':>12}")
print("-" * 92)
for f in (OLD_FLOOR, -2.0, -4.0, -6.0, -8.0, RELEVANCE_FLOOR, -9.5, -10.0):
    rk = sum(1 for s in rel if s > f)
    nk = sum(1 for s in neg if s > f)
    marker = "  <- OLD" if f == OLD_FLOOR else ("  <- NEW (RELEVANCE_FLOOR)" if f == RELEVANCE_FLOOR else "")
    print(f"{f:7.1f} | {rk:8d} {len(rel) - rk:11d} | {nk:13d} {len(neg) - nk:12d}{marker}")

print()
old_kept = sum(1 for s in rel if s > OLD_FLOOR)
new_kept = sum(1 for s in rel if s > RELEVANCE_FLOOR)
new_fp = sum(1 for s in neg if s > RELEVANCE_FLOOR)

check(old_kept / len(rel) < 0.75,
      f"OLD floor 0.0 drops a large share of relevant chunks ({len(rel) - old_kept}/{len(rel)})")
check(new_kept / len(rel) >= 0.90,
      f"NEW floor {RELEVANCE_FLOOR} keeps >=90% of relevant chunks ({new_kept}/{len(rel)})")
check(new_fp == 0,
      f"NEW floor {RELEVANCE_FLOOR} lets through zero hard negatives ({new_fp} FP of {len(neg)})")
check(new_kept > old_kept,
      f"NEW floor recovers {new_kept - old_kept} relevant chunks the old floor discarded")
check(RELEVANCE_FLOOR - max(neg) >= 0.5,
      f"NEW floor keeps a margin above the worst true negative "
      f"({RELEVANCE_FLOOR} vs {max(neg):.2f})")

# Every natural/meta/rambling relevant phrasing (the reported bug) must survive.
conversational = [(s, q) for s, r, st, q, _ in scored if r and st in ("natural", "meta", "rambling")]
dropped_now = [q for s, q in conversational if s <= RELEVANCE_FLOOR]
check(len(dropped_now) <= 1,
      f"at most one awkward conversational phrasing still floored "
      f"(now: {dropped_now or 'none'})")

print("\n" + "=" * 92)
print(f"  {'ALL CHECKS PASSED' if not FAIL else 'FAILURES: ' + '; '.join(FAIL)}")
if FAIL:
    raise SystemExit(1)
