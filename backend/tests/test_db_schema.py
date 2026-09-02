from backend.app.models import Base
from backend.app.models import Activity, AuditLog, ComplianceRegister, CorpusDoc, Decision, FlowEdge, FlowNode, Library, LibraryVersion, Project, SimulationRun, Signoff, TrailEntry, User, Zone
from backend.app.schemas import Brief, Decision as DecisionSchema, SimulationOutput


EXPECTED_TABLES = {
    'projects',
    'briefs',
    'decisions',
    'simulations',
    'flow_nodes',
    'flow_edges',
    'zones',
    'activities',
    'trail_entries',
    'corpus_docs',
    'libraries',
    'library_versions',
    'compliance_registers',
    'users',
    'signoffs',
    'audit_log',
    'cache_entries',
}


def test_database_metadata_contains_required_tables():
    actual = {table.name for table in Base.metadata.sorted_tables}
    assert EXPECTED_TABLES.issubset(actual)
    assert 'embedding' in CorpusDoc.__table__.columns
    assert 'library_version_id' in SimulationRun.__table__.columns
    assert 'version' in Library.__table__.columns


def test_schema_models_can_be_created():
    brief = Brief(
        project_name='Delta DC 1',
        city='Pune',
        tier='Tier III',
        it_load_mw=12.5,
        client='Alpha',
        questions=[],
    )
    decision = DecisionSchema(
        id='d1',
        question='Who supplies the generators?',
        answer='Owner-furnished equipment',
        impact='Changes procurement path',
    )
    output = SimulationOutput(
        project_meta={'project_name': 'Delta DC 1'},
        questions=[],
        decisions=[decision],
        flow={'nodes': [], 'edges': []},
        statutory_pathway=[],
        equipment_counts=[],
        long_lead_register=[],
        activities=[],
        commissioning=[],
        zones=[],
        reasoning_trail=[],
        quality={'dcma_summary': 'ok', 'governance_complete': True},
        flags=[],
    )

    assert brief.project_name == 'Delta DC 1'
    assert decision.answer == 'Owner-furnished equipment'
    assert output.project_meta['project_name'] == 'Delta DC 1'


def test_ensure_tables_creates_the_vector_extension_before_the_tables():
    """A Postgres deployment without pgvector must not lose every table.

    Two corpus tables declare Vector(1536). Postgres rejects that type when the extension is
    missing, which fails the entire create_all — so `run_states` and `usage_counters` would be
    missing too, and metering fails closed, so the API would 503 every request. The extension
    has to be created first, in the same call.
    """
    import backend.app.database as db

    issued = []

    class FakeConn:
        def exec_driver_sql(self, sql):
            issued.append(sql)

    class FakeCtx:
        def __enter__(self): return FakeConn()
        def __exit__(self, *a): return False

    class FakeEngine:
        def begin(self): return FakeCtx()

    created = []
    original_engine, original_url = db.engine, db.DATABASE_URL
    original_create = db.Base.metadata.create_all
    try:
        db.DATABASE_URL = 'postgresql://user@host/db'
        db.engine = FakeEngine()
        db.Base.metadata.create_all = lambda bind=None: created.append(bind)
        db.reset_tables_ready()
        db.ensure_tables()
    finally:
        db.engine, db.DATABASE_URL = original_engine, original_url
        db.Base.metadata.create_all = original_create
        db.reset_tables_ready()

    assert any('CREATE EXTENSION IF NOT EXISTS vector' in s for s in issued), (
        'pgvector extension was not created, so create_all would fail on Postgres'
    )
    assert created, 'tables were never created'


def test_init_db_and_ensure_tables_are_the_same_path():
    """They diverged once: init_db created the extension and ensure_tables did not, and only
    ensure_tables was ever called. Divergence survives when the correct path is the unused one."""
    import backend.app.database as db

    calls = []
    original = db.ensure_tables
    try:
        db.ensure_tables = lambda: calls.append('called')
        db.init_db()
    finally:
        db.ensure_tables = original
    assert calls == ['called']
