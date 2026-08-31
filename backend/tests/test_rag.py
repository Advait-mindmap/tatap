"""Tests for corpus ingestion and pgvector retrieval.

Runs on sqlite: the pgvector Vector column round-trips there, and retrieval falls back to an
identical ranking computed in Python, so the whole pipeline is exercised without Postgres.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.app.models import Base, CorpusChunk, CorpusDoc
from backend.app.rag import (
    EMBEDDING_DIM,
    CorpusKind,
    LexicalHashEmbedder,
    chunk_text,
    cosine,
    get_embedder,
    ingest_document,
    ingest_seed_corpus,
    retrieve,
)


@pytest.fixture
def session():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def embedder():
    return LexicalHashEmbedder()


# ------------------------------------------------------------------------ embeddings


def test_embedding_dimension_matches_the_vector_column():
    vec = LexicalHashEmbedder().embed('transformer lead time')
    assert len(vec) == EMBEDDING_DIM
    assert CorpusChunk.__table__.columns['embedding'].type.dim == EMBEDDING_DIM


def test_embedding_is_deterministic():
    """Reproducible mode requires identical output on re-run (SIMULATION_AND_REASONING.md §8)."""
    a, b = LexicalHashEmbedder(), LexicalHashEmbedder()
    assert a.embed('CEIG energisation approval') == b.embed('CEIG energisation approval')


def test_embedding_is_unit_length_and_similar_text_scores_higher():
    e = LexicalHashEmbedder()
    q = e.embed('chilled water piping pressure test')
    close = e.embed('pressure test of chilled water piping')
    far = e.embed('environmental clearance from SEIAA')
    assert abs(sum(v * v for v in q) - 1.0) < 1e-9
    assert cosine(q, close) > cosine(q, far)


def test_embedder_is_declared_non_semantic():
    """The limitation must be discoverable in code, not just in a comment."""
    assert get_embedder().is_semantic is False


def test_requesting_a_semantic_provider_fails_loudly(monkeypatch):
    monkeypatch.setenv('EMBED_PROVIDER', 'openai')
    with pytest.raises(NotImplementedError, match='not implemented'):
        get_embedder()


# --------------------------------------------------------------------------- chunking


def test_chunking_covers_the_text_with_overlap():
    text = '\n\n'.join(f'Paragraph {i} about data centre commissioning.' * 6 for i in range(12))
    chunks = chunk_text(text, chunk_size=400, overlap=80)
    assert len(chunks) > 1
    assert all(c.strip() for c in chunks)
    # Nothing is silently dropped: the first and last content survive.
    assert 'Paragraph 0' in chunks[0]
    assert 'Paragraph 11' in chunks[-1]


def test_short_text_is_a_single_chunk():
    assert chunk_text('one short line') == ['one short line']
    assert chunk_text('   ') == []


def test_overlap_must_be_smaller_than_chunk_size():
    with pytest.raises(ValueError):
        chunk_text('x' * 500, chunk_size=100, overlap=100)


# -------------------------------------------------------------------------- ingestion


def test_ingest_document_persists_doc_and_embedded_chunks(session, embedder):
    doc = ingest_document(
        session,
        title='Transformer procurement note',
        content='HV transformer lead times drive RFS. ' * 80,
        source='test',
        kind=CorpusKind.REAL_EXECUTION,
        project_name='Project A',
        city='Navi Mumbai',
        tier='Tier III',
        embedder=embedder,
        chunk_size=300,
        overlap=50,
    )
    session.commit()

    assert doc.id is not None
    assert doc.embed_status == 'embedded'
    assert len(doc.embedding) == EMBEDDING_DIM

    chunks = session.execute(
        select(CorpusChunk).where(CorpusChunk.doc_id == doc.id)
    ).scalars().all()
    assert len(chunks) > 1
    assert all(len(c.embedding) == EMBEDDING_DIM for c in chunks)
    assert all(c.embed_model == embedder.name for c in chunks)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_ingestion_never_marks_a_document_verified(session, embedder):
    """Verification is a human act in admin (ADMIN_SPEC.md §1), never a side effect of loading."""
    doc = ingest_document(
        session, title='t', content='body text here', source='s',
        kind=CorpusKind.REAL_EXECUTION, embedder=embedder,
    )
    assert doc.verified is False


def test_ingest_requires_a_real_corpus_kind(session, embedder):
    with pytest.raises(ValueError, match='CorpusKind'):
        ingest_document(
            session, title='t', content='c', source='s', kind='real_execution',  # type: ignore
            embedder=embedder,
        )


def test_seed_corpus_contains_no_invented_real_executions(session, embedder):
    """The seed must not fabricate precedent: an invented project would be cited as evidence."""
    result = ingest_seed_corpus(session, embedder=embedder)
    session.commit()

    assert result['ingested'] > 0
    assert result['real_execution_docs'] == 0

    kinds = {d.kind for d in session.execute(select(CorpusDoc)).scalars().all()}
    assert CorpusKind.REAL_EXECUTION.value not in kinds
    assert kinds <= {CorpusKind.PROJECT_DOCUMENTATION.value, CorpusKind.STANDARD.value}

    assert any('NO REAL-EXECUTION DOCUMENTS' in w for w in result['warnings'])
    assert any('LEXICAL' in w for w in result['warnings'])


def test_seed_corpus_ingestion_is_idempotent(session, embedder):
    first = ingest_seed_corpus(session, embedder=embedder)
    second = ingest_seed_corpus(session, embedder=embedder)
    assert first['ingested'] > 0
    assert second['ingested'] == 0


# -------------------------------------------------------------------------- retrieval


def _seed_three(session, embedder):
    ingest_document(
        session, title='CEIG energisation', source='doc-a', kind=CorpusKind.STANDARD,
        content='Energisation of an HT installation requires CEIG approval from the state '
                'electrical inspectorate before power is applied.',
        embedder=embedder,
    )
    ingest_document(
        session, title='Chiller commissioning', source='doc-b',
        kind=CorpusKind.PROJECT_DOCUMENTATION,
        content='Chilled water piping is pressure tested and flushed before CRAH units are '
                'installed in the data hall.',
        embedder=embedder,
    )
    ingest_document(
        session, title='Real project schedule', source='doc-c', kind=CorpusKind.REAL_EXECUTION,
        content='On this delivered project the HV transformer arrived late and the '
                'energisation milestone slipped by six weeks.',
        project_name='Project X', city='Navi Mumbai', tier='Tier III', embedder=embedder,
    )
    session.commit()


def test_retrieval_ranks_the_relevant_document_first(session, embedder):
    _seed_three(session, embedder)
    result = retrieve(session, 'CEIG approval to energise HT installation', k=3, embedder=embedder)
    assert len(result) > 0
    assert result.hits[0].doc_title == 'CEIG energisation'
    assert result.hits[0].score > 0
    assert result.backend == 'python-cosine'


def test_retrieval_scores_descend(session, embedder):
    _seed_three(session, embedder)
    result = retrieve(session, 'pressure test chilled water piping', k=3, embedder=embedder)
    scores = [h.score for h in result.hits]
    assert scores == sorted(scores, reverse=True)


def test_retrieval_can_filter_to_real_executions_only(session, embedder):
    _seed_three(session, embedder)
    result = retrieve(
        session, 'transformer energisation', k=5,
        kinds=[CorpusKind.REAL_EXECUTION], embedder=embedder,
    )
    assert result.hits
    assert all(h.kind == CorpusKind.REAL_EXECUTION.value for h in result.hits)
    assert all(h.citable_as_precedent for h in result.hits)


def test_only_real_executions_are_citable_as_precedent(session, embedder):
    _seed_three(session, embedder)
    result = retrieve(session, 'energisation', k=5, embedder=embedder)
    for hit in result.hits:
        expected = hit.kind == CorpusKind.REAL_EXECUTION.value
        assert hit.citable_as_precedent is expected


def test_retrieval_warns_when_no_real_precedent_is_found(session, embedder):
    """A caller with no real-execution hit is not grounded and must be told."""
    ingest_document(
        session, title='Standards only', source='s', kind=CorpusKind.STANDARD,
        content='NBC 2016 Part 4 governs fire and life safety.', embedder=embedder,
    )
    session.commit()
    result = retrieve(session, 'fire safety NBC', k=3, embedder=embedder)
    assert result.hits
    assert result.precedent_hits == []
    assert any('NO REAL-EXECUTION PRECEDENT' in w for w in result.warnings)


def test_retrieval_warns_when_nothing_is_verified(session, embedder):
    _seed_three(session, embedder)
    result = retrieve(session, 'energisation', k=3, embedder=embedder)
    assert any('human-verified' in w for w in result.warnings)


def test_empty_corpus_returns_no_hits_with_a_warning(session, embedder):
    result = retrieve(session, 'anything at all', k=5, embedder=embedder)
    assert len(result) == 0
    assert any('No corpus chunks matched' in w for w in result.warnings)


def test_hit_converts_to_a_trail_source_ref(session, embedder):
    """TrailEntry.sources needs a citation carrying the grounding status."""
    _seed_three(session, embedder)
    hit = retrieve(session, 'transformer slipped energisation', k=1, embedder=embedder).hits[0]
    ref = hit.to_source_ref()
    assert ref['ref'].startswith('corpus:')
    assert set(ref) == {
        'ref', 'doc_id', 'chunk_id', 'kind', 'score', 'verified', 'citable_as_precedent'
    }


def test_city_filter_restricts_results(session, embedder):
    _seed_three(session, embedder)
    assert retrieve(session, 'transformer', k=5, city='Navi Mumbai', embedder=embedder).hits
    assert retrieve(session, 'transformer', k=5, city='Chennai', embedder=embedder).hits == []
