"""
Embedding generation for RAG — dense (BAAI/bge-small-en-v1.5, 384-dim) via
fastembed's TextEmbedding, and sparse (Qdrant-native BM25, IDF-modified) via
fastembed's SparseTextEmbedding. Both underlying models are loaded once
(loading is the expensive part) and reused for every call.

count_tokens() uses the SAME model's vocabulary/tokenization rules, but via
an INDEPENDENT tokenizer instance — not fastembed's own token_count() /
tokenize(), both of which silently truncate at the model's 512-token max
sequence length (confirmed empirically: a 2000-word input reports back
exactly 512 either way). That makes them useless for chunking decisions,
whose whole job is detecting text LONGER than that. A fresh
tokenizers.Tokenizer.from_pretrained(...) instance has no truncation
configured by default and reports the true length; the embedding model's
own (truncating) tokenizer is left untouched since embedding genuinely
needs that truncation to keep inputs at the model's fixed input size.
"""

from functools import lru_cache

from fastembed import SparseTextEmbedding, TextEmbedding
from qdrant_client import models as qm
from tokenizers import Tokenizer

from app.services.rag.collection_setup import DENSE_VECTOR_SIZE

DENSE_MODEL_NAME = "BAAI/bge-small-en-v1.5"
SPARSE_MODEL_NAME = "Qdrant/bm25"


@lru_cache(maxsize=1)
def _dense_model() -> TextEmbedding:
    return TextEmbedding(DENSE_MODEL_NAME)


@lru_cache(maxsize=1)
def _sparse_model() -> SparseTextEmbedding:
    return SparseTextEmbedding(SPARSE_MODEL_NAME)


@lru_cache(maxsize=1)
def _counting_tokenizer() -> Tokenizer:
    return Tokenizer.from_pretrained(DENSE_MODEL_NAME)


def count_tokens(text: str) -> int:
    """
    Real, UNTRUNCATED token count for `text`, via a dedicated tokenizer
    instance (see module docstring for why fastembed's own token_count()
    can't be used here).
    """
    return len(_counting_tokenizer().encode(text).ids)


def embed_dense(texts: list[str]) -> list[list[float]]:
    """One 384-dim vector per input text, same order."""
    if not texts:
        return []
    vectors = list(_dense_model().embed(texts))
    assert all(len(v) == DENSE_VECTOR_SIZE for v in vectors), (
        f"dense embedding size mismatch — expected {DENSE_VECTOR_SIZE}"
    )
    return [v.tolist() for v in vectors]


def embed_sparse(texts: list[str]) -> list[qm.SparseVector]:
    """One BM25 sparse vector per input text, same order."""
    if not texts:
        return []
    results = list(_sparse_model().embed(texts))
    return [
        qm.SparseVector(indices=r.indices.tolist(), values=r.values.tolist())
        for r in results
    ]
