"""Corpus ingestion: document -> chunks -> embeddings -> Postgres/pgvector."""

from __future__ import annotations

import os
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import CorpusChunk, CorpusDoc
from backend.app.rag.embeddings import Embedder, get_embedder


class CorpusKind(str, Enum):
    """What a corpus document actually is. Governs whether it may be cited as precedent."""

    #: A real delivered project: historical schedule, actuals, lessons learned, method
    #: statement. The only kind that may be cited as real-execution precedent.
    REAL_EXECUTION = 'real_execution'

    #: A published standard or statute (Uptime, TIA-942, NBC 2016, IS codes, CEA regs).
    STANDARD = 'standard'

    #: This repo's own domain documentation. Traceable, but it is the brief for the product,
    #: not evidence of how a project was executed.
    PROJECT_DOCUMENTATION = 'project_documentation'

    #: Illustrative filler. Must never be cited as precedent.
    SYNTHETIC_PLACEHOLDER = 'synthetic_placeholder'


#: Only this kind satisfies "grounded in real executions".
CITABLE_AS_PRECEDENT = frozenset({CorpusKind.REAL_EXECUTION})


def corpus_version() -> str:
    return os.getenv('CORPUS_VERSION', 'v1')


def chunk_text(text: str, chunk_size: int = 1200, overlap: int = 150) -> List[str]:
    """Split text into overlapping chunks, preferring paragraph boundaries.

    Overlap keeps a fact that straddles a boundary retrievable from either side.
    """
    text = (text or '').strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    if overlap >= chunk_size:
        raise ValueError('overlap must be smaller than chunk_size')

    chunks: List[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            # Prefer a paragraph break, then a sentence break, in the last quarter of the window.
            window_start = start + (chunk_size * 3) // 4
            para = text.rfind('\n\n', window_start, end)
            stop = text.rfind('. ', window_start, end)
            if para != -1:
                end = para + 2
            elif stop != -1:
                end = stop + 2
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def ingest_document(
    session: Session,
    *,
    title: str,
    content: str,
    source: str,
    kind: CorpusKind,
    project_name: str = '',
    city: str = '',
    tier: str = '',
    tags: Optional[Sequence[str]] = None,
    embedder: Optional[Embedder] = None,
    chunk_size: int = 1200,
    overlap: int = 150,
) -> CorpusDoc:
    """Ingest one document: chunk it, embed each chunk, persist doc + chunks.

    `verified` is deliberately never set here. Ingestion is mechanical; verification is a human
    act performed in admin (ADMIN_SPEC.md §1).
    """
    if not isinstance(kind, CorpusKind):
        raise ValueError(f'kind must be a CorpusKind, got {type(kind).__name__}')

    embedder = embedder or get_embedder()
    version = corpus_version()

    doc = CorpusDoc(
        source=source,
        project_name=project_name,
        city=city,
        tier=tier,
        title=title,
        content=content,
        kind=kind.value,
        verified=False,
        corpus_version=version,
        embed_status='pending',
        tags=list(tags or []),
    )
    session.add(doc)
    session.flush()

    pieces = chunk_text(content, chunk_size=chunk_size, overlap=overlap)
    vectors = embedder.embed_batch(pieces)
    for index, (piece, vector) in enumerate(zip(pieces, vectors)):
        session.add(
            CorpusChunk(
                doc_id=doc.id,
                chunk_index=index,
                text=piece,
                embedding=vector,
                embed_status='embedded',
                embed_model=embedder.name,
                corpus_version=version,
            )
        )

    # A document-level vector, so a doc can be matched without loading its chunks.
    doc.embedding = embedder.embed(f'{title}\n\n{content}') if content.strip() else None
    doc.embed_status = 'embedded' if pieces else 'empty'
    session.flush()
    return doc


def ingest_seed_corpus(
    session: Session, embedder: Optional[Embedder] = None
) -> Dict[str, Any]:
    """Load the seed corpus.

    The seed contains NO invented "real project" documents, and that is deliberate. The
    reasoning trail cites the precedent it used, so a fabricated project schedule in the corpus
    would surface to a planner as though a real delivered project supported the plan. The seed
    therefore carries only this repo's own domain documentation and named public standards, and
    every one is marked with a kind that excludes it from being cited as precedent.

    The real-execution corpus is the client's to supply (INPUTS.md §4).
    """
    from backend.app.rag.seed_corpus import SEED_DOCUMENTS

    embedder = embedder or get_embedder()
    ingested: List[CorpusDoc] = []
    for spec in SEED_DOCUMENTS:
        existing = session.execute(
            select(CorpusDoc).where(CorpusDoc.title == spec['title'])
        ).scalars().first()
        if existing is not None:
            continue
        ingested.append(ingest_document(session, embedder=embedder, **spec))

    real_count = session.execute(
        select(CorpusDoc).where(CorpusDoc.kind == CorpusKind.REAL_EXECUTION.value)
    ).scalars().all()

    return {
        'ingested': len(ingested),
        'corpus_version': corpus_version(),
        'embed_model': embedder.name,
        'is_semantic': embedder.is_semantic,
        'real_execution_docs': len(real_count),
        'warnings': _seed_warnings(len(real_count), embedder),
    }


def _seed_warnings(real_execution_docs: int, embedder: Embedder) -> List[str]:
    warnings: List[str] = []
    if real_execution_docs == 0:
        warnings.append(
            'CORPUS HAS NO REAL-EXECUTION DOCUMENTS. DOMAIN_KNOWLEDGE.md §1 defines expertise '
            'here as preferring real project precedent and citing it. Until the client loads '
            'historical DC schedules and actuals (INPUTS.md §4), no simulation can cite real '
            'precedent, and its reasoning is generic rather than grounded.'
        )
    if not embedder.is_semantic:
        warnings.append(
            f'Embedder {embedder.name!r} is LEXICAL, not semantic: it matches shared vocabulary, '
            'not shared meaning. Retrieval will miss paraphrases. No embedding API is configured '
            'for this project.'
        )
    return warnings
