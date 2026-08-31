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
