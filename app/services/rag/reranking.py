"""
Cross-encoder reranking for RAG retrieval (Phase C).

Xenova/ms-marco-MiniLM-L-6-v2 is the ONNX-packaged export of
cross-encoder/ms-marco-MiniLM-L-6-v2 (same weights, converted for ONNX
Runtime) — used via fastembed's TextCrossEncoder rather than pulling in
sentence-transformers + torch for a model this pipeline already has an
ONNX-based embedding stack for (see embedding.py).

Scores are RAW, UNBOUNDED logits from the model's ranking head (fastembed
applies no sigmoid) — NOT a 0-1 probability, and NOT calibrated so that 0 is
the relevant/irrelevant boundary.

RELEVANCE_FLOOR — calibration
----------------------------
The original value (0.0, "the model's own zero-crossing") was a
misconception: this MS MARCO ranking model has no meaningful zero-crossing.
It was dropping ~35-55% of genuinely relevant chunks, especially for
questions phrased naturally rather than as keyword search
("What does the handbook say about remote work?" -> -0.3 to -6, below 0).

What the score distribution actually looks like (measured over labelled
query/chunk pairs — keyword, natural, meta and rambling phrasings; see
scripts/verify_relevance_floor.py):

  * UNRELATED pairs (query about topic X, chunk about an unrelated topic Y)
    cluster tightly at the bottom: essentially all <= -10.3, most around -11.
  * genuinely RELEVANT pairs span a huge range, roughly -9.5 up to +10, with
    a long tail of correct-but-loosely-phrased matches sitting well below 0.
  * SEMANTIC NEAR-MISSES (e.g. "parental leave policy" vs a vacation chunk,
    "expense deadline" vs a vacation-request-deadline chunk) land in between,
    around -8 to -10.5 — the model genuinely can't cleanly separate these
    from real matches, and neither could a person.

So the real decision boundary between "related at all" and "noise" is near
-10, not 0. RELEVANCE_FLOOR = -9.0 sits on the relevant side of that gap:
it keeps ~1.3 points of headroom above the worst unrelated pair, and
recovers ~90% of the relevant chunks the old 0.0 floor was silently
discarding.

The floor is a coarse NOISE gate, not the final relevance judge:
  * a query with genuinely no answer in the corpus -> its candidates score
    around -11, stay below the floor, retrieve() returns nothing, and the
    agent honestly says "not found".
  * a semantic near-miss may pass the floor and reach generation — that's
    acceptable, because generation is prompted to answer ONLY from what the
    chunks actually say and to return "the retrieved documents don't contain
    an answer to that" otherwise.
  * a small number of maximally-awkward phrasings (a question that shares
    almost no vocabulary with the chunk that answers it) can still fall
    below the floor. That is a residual limit of this small cross-encoder,
    not a mis-set threshold.
"""

from functools import lru_cache

from fastembed.rerank.cross_encoder import TextCrossEncoder

CROSS_ENCODER_MODEL_NAME = "Xenova/ms-marco-MiniLM-L-6-v2"

# Empirically calibrated against the measured score distribution for this
# specific model — see the module docstring. NOT the model's zero-crossing.
RELEVANCE_FLOOR = -9.0


@lru_cache(maxsize=1)
def _cross_encoder() -> TextCrossEncoder:
    return TextCrossEncoder(CROSS_ENCODER_MODEL_NAME)


def rerank_scores(query: str, documents: list[str]) -> list[float]:
    """One raw cross-encoder relevance score per document, same order as input."""
    if not documents:
        return []
    return list(_cross_encoder().rerank(query, documents))
