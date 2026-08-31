"""Embedding providers for corpus retrieval.

IMPORTANT LIMITATION
--------------------
No embedding API is configured for this project. The Base44 gateway wraps `Core.InvokeLLM`
only — it has no embeddings endpoint (see docs/BASE44_GATEWAY.md) — and OPENAI_API_KEY /
ANTHROPIC_API_KEY are both empty.

The default `LexicalHashEmbedder` below is therefore **lexical, not semantic**. It is a hashed
bag-of-words vector: it matches documents that share vocabulary with the query, and it will NOT
match a paraphrase that shares meaning but no words ("genset" vs "diesel generator"). It is
deterministic and offline, which makes it useful for tests and for reproducible mode, but
retrieval quality is materially below what a real embedding model gives.

Swap in a semantic provider before the corpus retrieval quality is relied on. The interface is
provider-agnostic precisely so that is a one-line change.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from typing import Iterable, List, Protocol

#: Must match the Vector(1536) column on corpus_docs/corpus_chunks.
EMBEDDING_DIM = 1536

_TOKEN_RE = re.compile(r'[a-z0-9]+')


class Embedder(Protocol):
    name: str
    is_semantic: bool

    def embed(self, text: str) -> List[float]: ...

    def embed_batch(self, texts: Iterable[str]) -> List[List[float]]: ...


def _tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall(text.lower())


class LexicalHashEmbedder:
    """Deterministic hashed bag-of-words. Lexical overlap only — NOT semantic similarity.

    Sublinear term-frequency weighting (1 + log tf) keeps a repeated word from dominating, and
    the vector is L2-normalised so cosine similarity is a plain dot product.
    """

    name = 'lexical-hash-v1'
    is_semantic = False

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self.dim = dim

    def embed(self, text: str) -> List[float]:
        counts: dict[int, float] = {}
        for token in _tokenize(text):
            digest = hashlib.blake2b(token.encode('utf-8'), digest_size=8).digest()
            idx = int.from_bytes(digest, 'big') % self.dim
            counts[idx] = counts.get(idx, 0.0) + 1.0

        vec = [0.0] * self.dim
        for idx, tf in counts.items():
            vec[idx] = 1.0 + math.log(tf)

        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    def embed_batch(self, texts: Iterable[str]) -> List[List[float]]:
        return [self.embed(t) for t in texts]


def cosine(a: List[float], b: List[float]) -> float:
    """Cosine similarity. Inputs from LexicalHashEmbedder are already unit vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / math.sqrt(na * nb)


def get_embedder() -> Embedder:
    """Return the configured embedder.

    EMBED_PROVIDER is read for forward-compatibility; only the lexical provider exists today,
    and asking for a semantic one fails loudly rather than silently degrading to keyword search.
    """
    provider = os.getenv('EMBED_PROVIDER', 'lexical').lower()
    if provider in ('lexical', 'lexical-hash', ''):
        return LexicalHashEmbedder()
    raise NotImplementedError(
        f'Embedding provider {provider!r} is not implemented. Only the offline lexical '
        'embedder exists; no semantic embedding API is configured for this project.'
    )
