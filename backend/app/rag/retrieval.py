"""Corpus retrieval over pgvector, with an in-Python fallback.

On Postgres the ranking is done by pgvector's cosine-distance operator so it stays in the
database and can use an index. Elsewhere (sqlite in tests) the same ranking is computed in
Python. Both paths return identical structures, so callers never branch on the backend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import CorpusChunk, CorpusDoc
from backend.app.rag.embeddings import Embedder, cosine, get_embedder
from backend.app.rag.ingest import CITABLE_AS_PRECEDENT, CorpusKind


@dataclass
class RetrievalHit:
    """One retrieved chunk, with everything the reasoning trail needs to cite it."""

    chunk_id: int
    doc_id: int
    doc_title: str
    source: str
    kind: str
    text: str
    score: float
    chunk_index: int
    verified: bool
    project_name: str = ''
    city: str = ''
    tier: str = ''

    @property
    def citable_as_precedent(self) -> bool:
        """True only for real executions. A standard is a citation, not a precedent."""
        return self.kind in {k.value for k in CITABLE_AS_PRECEDENT}

    @property
    def citation(self) -> str:
        return f'corpus:{self.doc_id}#{self.chunk_index} ({self.kind}) {self.doc_title}'

    def to_source_ref(self) -> Dict[str, Any]:
        """The shape a TrailEntry.sources element takes."""
        return {
            'ref': self.citation,
            'doc_id': self.doc_id,
            'chunk_id': self.chunk_id,
            'kind': self.kind,
            'score': round(self.score, 6),
            'verified': self.verified,
            'citable_as_precedent': self.citable_as_precedent,
        }


@dataclass
class RetrievalResult:
    hits: List[RetrievalHit] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    backend: str = ''
    embed_model: str = ''
    is_semantic: bool = False

    @property
    def precedent_hits(self) -> List[RetrievalHit]:
        return [h for h in self.hits if h.citable_as_precedent]

    def __len__(self) -> int:
        return len(self.hits)


def _is_postgres(session: Session) -> bool:
    bind = session.get_bind()
    return bool(bind is not None and bind.dialect.name == 'postgresql')


def retrieve(
    session: Session,
    query: str,
    *,
    k: int = 5,
    kinds: Optional[Sequence[CorpusKind]] = None,
    city: Optional[str] = None,
    embedder: Optional[Embedder] = None,
    min_score: float = 0.0,
) -> RetrievalResult:
    """Retrieve the k most similar corpus chunks for `query`.

    Warnings are part of the contract, not decoration: a caller that gets zero real-execution
    hits is not grounded in real precedent and needs to know before it cites anything.
    """
    embedder = embedder or get_embedder()
    query_vec = embedder.embed(query)

    kind_values = [k_.value for k_ in kinds] if kinds else None
    use_pg = _is_postgres(session)

    if use_pg:
        hits = _retrieve_pgvector(session, query_vec, k, kind_values, city, min_score)
        backend = 'pgvector'
    else:
        hits = _retrieve_python(session, query_vec, k, kind_values, city, min_score)
        backend = 'python-cosine'

    return RetrievalResult(
        hits=hits,
        warnings=_warnings_for(hits, embedder),
        backend=backend,
        embed_model=embedder.name,
        is_semantic=embedder.is_semantic,
    )


def _base_query(kind_values: Optional[List[str]], city: Optional[str]):
    stmt = select(CorpusChunk, CorpusDoc).join(CorpusDoc, CorpusChunk.doc_id == CorpusDoc.id)
    if kind_values:
        stmt = stmt.where(CorpusDoc.kind.in_(kind_values))
    if city:
        stmt = stmt.where(CorpusDoc.city == city)
    return stmt


def _retrieve_pgvector(
    session: Session,
    query_vec: List[float],
    k: int,
    kind_values: Optional[List[str]],
    city: Optional[str],
    min_score: float,
) -> List[RetrievalHit]:
    # cosine_distance = 1 - cosine_similarity, so ascending distance is descending similarity.
    distance = CorpusChunk.embedding.cosine_distance(query_vec)
    stmt = (
        _base_query(kind_values, city)
        .add_columns(distance.label('distance'))
        .where(CorpusChunk.embedding.isnot(None))
        .order_by(distance)
        .limit(k)
    )
    hits = []
    for chunk, doc, dist in session.execute(stmt).all():
        score = 1.0 - float(dist)
        if score >= min_score:
            hits.append(_to_hit(chunk, doc, score))
    return hits


def _retrieve_python(
    session: Session,
    query_vec: List[float],
    k: int,
    kind_values: Optional[List[str]],
    city: Optional[str],
    min_score: float,
) -> List[RetrievalHit]:
    rows = session.execute(_base_query(kind_values, city)).all()
    scored = []
    for chunk, doc in rows:
        if chunk.embedding is None:
            continue
        score = cosine(query_vec, list(chunk.embedding))
        if score >= min_score:
            scored.append(_to_hit(chunk, doc, score))
    # Tie-break on (doc_id, chunk_index) so ordering is deterministic for reproducible mode.
    scored.sort(key=lambda h: (-h.score, h.doc_id, h.chunk_index))
    return scored[:k]


def _to_hit(chunk: CorpusChunk, doc: CorpusDoc, score: float) -> RetrievalHit:
    return RetrievalHit(
        chunk_id=chunk.id,
        doc_id=doc.id,
        doc_title=doc.title,
        source=doc.source,
        kind=doc.kind,
        text=chunk.text,
        score=score,
        chunk_index=chunk.chunk_index,
        verified=doc.verified,
        project_name=doc.project_name,
        city=doc.city,
        tier=doc.tier,
    )


def _warnings_for(hits: List[RetrievalHit], embedder: Embedder) -> List[str]:
    warnings: List[str] = []
    if not hits:
        warnings.append('No corpus chunks matched the query.')
        return warnings
    if not any(h.citable_as_precedent for h in hits):
        kinds = sorted({h.kind for h in hits})
        warnings.append(
            'NO REAL-EXECUTION PRECEDENT among the retrieved chunks '
            f'(kinds present: {", ".join(kinds)}). These may be cited as references but NOT as '
            'evidence of how a project was executed. DOMAIN_KNOWLEDGE.md §1.'
        )
    if not any(h.verified for h in hits):
        warnings.append('None of the retrieved documents has been human-verified in admin.')
    if not embedder.is_semantic:
        warnings.append(
            f'Ranking used the LEXICAL embedder {embedder.name!r}: matches are on shared '
            'vocabulary, not shared meaning. Paraphrased precedent will be missed.'
        )
    return warnings
