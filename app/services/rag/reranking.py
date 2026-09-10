"""
Cross-encoder reranking for RAG retrieval (Phase C).

Xenova/ms-marco-MiniLM-L-6-v2 is the ONNX-packaged export of
cross-encoder/ms-marco-MiniLM-L-6-v2 (same weights, converted for ONNX
Runtime) — used via fastembed's TextCrossEncoder rather than pulling in
sentence-transformers + torch for a model this pipeline already has an
ONNX-based embedding stack for (see embedding.py).

Scores are RAW, UNBOUNDED logits from the model's ranking head — NOT a
0-1 probability. Empirically (see scripts/verify_retrieval.py): a clearly
relevant match scores strongly positive (~8+), an unrelated one strongly
negative (~-11), a tangential one lands in between. There's no fixed
"good" cutoff the model documents, so RELEVANCE_FLOOR uses the model's own
zero-crossing: positive means "the model considers this more relevant than
not," negative means the opposite. That's the simplest principled floor
available without hand-tuning against a labeled eval set we don't have yet.
"""

from functools import lru_cache

from fastembed.rerank.cross_encoder import TextCrossEncoder

CROSS_ENCODER_MODEL_NAME = "Xenova/ms-marco-MiniLM-L-6-v2"

# See module docstring — the model's own zero-crossing, not a tuned value.
RELEVANCE_FLOOR = 0.0


@lru_cache(maxsize=1)
def _cross_encoder() -> TextCrossEncoder:
    return TextCrossEncoder(CROSS_ENCODER_MODEL_NAME)


def rerank_scores(query: str, documents: list[str]) -> list[float]:
    """One raw cross-encoder relevance score per document, same order as input."""
    if not documents:
        return []
    return list(_cross_encoder().rerank(query, documents))
