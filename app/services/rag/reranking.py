"""
Cross-encoder reranking for RAG retrieval (Phase C).

Xenova/ms-marco-MiniLM-L-12-v2 is the ONNX-packaged export of
cross-encoder/ms-marco-MiniLM-L-12-v2 (same weights, converted for ONNX
Runtime) — used via fastembed's TextCrossEncoder rather than pulling in
sentence-transformers + torch for a model this pipeline already has an
ONNX-based embedding stack for (see embedding.py).

L-12 vs the previous L-6: same MS MARCO training data and ranking objective,
but 12 transformer layers instead of 6 — more expressive, better separation
between relevant and near-miss chunks, at a modest latency cost (~2x slower
on CPU for the reranking step, negligible at TOP_K=6 candidate counts).

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

NOTE: RELEVANCE_FLOOR = -9.0 was calibrated against the L-6 model's score
distribution (see scripts/verify_relevance_floor.py). The L-12 model's raw
logit range will differ — this value is a reasonable starting point but
should be re-measured against the same labelled query/chunk set once the
L-12 model has been exercised in your environment. If retrieval becomes
too permissive or too restrictive, run verify_relevance_floor.py to
re-calibrate.

The floor is a coarse NOISE gate, not the final relevance judge:
  * a query with genuinely no answer in the corpus -> its candidates score
    well below the floor, retrieve() returns nothing, and the agent honestly
    says "not found".
  * a semantic near-miss may pass the floor and reach generation — that's
    acceptable, because generation is prompted to answer ONLY from what the
    chunks actually say and to return "the retrieved documents don't contain
    an answer to that" otherwise.
"""

from functools import lru_cache

from fastembed.rerank.cross_encoder import TextCrossEncoder

CROSS_ENCODER_MODEL_NAME = "Xenova/ms-marco-MiniLM-L-12-v2"

# Calibrated against the L-6 model's score distribution — see the module
# docstring note above. Re-run scripts/verify_relevance_floor.py to
# re-calibrate for L-12 once the model has been exercised.
RELEVANCE_FLOOR = -9.8


@lru_cache(maxsize=1)
def _cross_encoder() -> TextCrossEncoder:
    return TextCrossEncoder(CROSS_ENCODER_MODEL_NAME)


def rerank_scores(query: str, documents: list[str]) -> list[float]:
    """One raw cross-encoder relevance score per document, same order as input."""
    if not documents:
        return []
    return list(_cross_encoder().rerank(query, documents))
