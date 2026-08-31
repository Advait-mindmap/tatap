"""Corpus ingestion and retrieval (pgvector).

DOMAIN_KNOWLEDGE.md §1: "The expert is only as good as its corpus." Every simulation decision
retrieves from here and cites the source in its reasoning trail, and "expert based on real
executions" means preferring real project precedent over generic norms and saying which
precedent was used.

That makes `CorpusKind` load-bearing rather than cosmetic: a retrieved chunk that is a standard
or a placeholder must never be cited as if it were a real execution. `RetrievalResult.warnings`
carries that up to the caller.
"""

from __future__ import annotations

from backend.app.rag.embeddings import (
    EMBEDDING_DIM,
    Embedder,
    LexicalHashEmbedder,
    cosine,
    get_embedder,
)
from backend.app.rag.ingest import CorpusKind, chunk_text, ingest_document, ingest_seed_corpus
from backend.app.rag.retrieval import RetrievalHit, RetrievalResult, retrieve

__all__ = [
    'EMBEDDING_DIM',
    'CorpusKind',
    'Embedder',
    'LexicalHashEmbedder',
    'RetrievalHit',
    'RetrievalResult',
    'chunk_text',
    'cosine',
    'get_embedder',
    'ingest_document',
    'ingest_seed_corpus',
    'retrieve',
]
