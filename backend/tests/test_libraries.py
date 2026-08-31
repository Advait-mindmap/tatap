"""Tests for the versioned domain libraries and their provenance guarantees.

The point of these tests is not that the numbers are right — they are invented and definitely
are not. The point is that the system knows they are invented and refuses to let them pass as
verified fact.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.app.libraries import (
    REQUIRED_LIBRARIES,
    LibraryError,
    all_entries,
    available_cities,
    library_version,
    load_all,
    load_city_pathway,
    load_library,
    verification_report,
)
from backend.app.libraries.provenance import (
    Origin,
    UnverifiedDomainDataError,
    VerificationStatus,
    assert_usable_in_live_plan,
    is_verified,
    provenance,
    unverified_entries,
)
from backend.app.libraries.registry import register_compliance_registers, register_libraries
from backend.app.models import Base, ComplianceRegister, Library, LibraryVersion


@pytest.fixture
def session():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


# --------------------------------------------------------------------------- loading


def test_every_required_library_loads():
    for name in REQUIRED_LIBRARIES:
        data = load_library(name)
        assert data['entries'], f'{name} has no entries'
        assert data['library_version'] == library_version()


def test_load_all_includes_city_pathways():
    libs = load_all()
    assert set(REQUIRED_LIBRARIES).issubset(libs)
    assert libs['city_pathways']['entries']
    assert 'navi_mumbai' in available_cities()


def test_unknown_city_raises_rather_than_guessing():
    """DOMAIN_KNOWLEDGE.md §5: the pathway is per-city data, never inferred."""
    with pytest.raises(LibraryError, match='No statutory pathway'):
        load_city_pathway('atlantis')


def test_library_version_mismatch_is_rejected(monkeypatch):
    load_library.cache_clear()
    monkeypatch.setenv('LIBRARY_VERSION', 'v99')
    try:
        with pytest.raises(LibraryError, match='LIBRARY_VERSION'):
            load_library('fragnets')
    finally:
        load_library.cache_clear()


# ------------------------------------------------------------------- provenance guarantees


def test_every_entry_in_every_library_declares_provenance():
    """A library entry with no provenance is exactly the plausible-looking filler the spec bans."""
    for entry in all_entries():
        prov = entry.get('provenance')
        assert prov, f'{entry.get("id")} has no provenance'
        assert prov['origin'] in {o.value for o in Origin}
        assert prov['verification_status'] in {s.value for s in VerificationStatus}


def test_nothing_in_the_seed_libraries_is_marked_verified():
    """Verification is a human act in admin. Seed data must never ship pre-approved."""
    for entry in all_entries():
        assert not is_verified(entry), f'{entry.get("id")} ships marked verified'
        assert entry['provenance']['verified_by'] is None


def test_invented_entries_carry_an_explicit_warning():
    invented = [e for e in all_entries()
                if e['provenance']['origin'] == Origin.MODEL_GENERATED.value]
    assert invented, 'expected the seed to contain model-generated entries'
    for entry in invented:
        assert 'INVENTED' in entry['provenance'].get('warning', ''), entry.get('id')


def test_durations_and_lead_times_are_all_flagged_as_invented():
    """The dangerous values specifically: they land on the critical path looking researched."""
    for entry in load_library('equipment_lead_times')['entries']:
        assert entry['provenance']['origin'] == Origin.MODEL_GENERATED.value
        assert 'typical_weeks' in entry
    for entry in load_library('productivity_norms')['entries']:
        assert entry['provenance']['origin'] == Origin.MODEL_GENERATED.value


def test_spec_transcribed_entries_cite_the_document_they_came_from():
    transcribed = [e for e in all_entries()
                   if e['provenance']['origin'] == Origin.SPEC_TRANSCRIBED.value]
    assert transcribed
    for entry in transcribed:
        assert entry['provenance']['source_ref'].startswith('docs/'), entry.get('id')


def test_unverified_data_cannot_drive_a_live_plan():
    """The gate that keeps invented numbers out of a client's schedule."""
    with pytest.raises(UnverifiedDomainDataError, match='unverified domain entries'):
        assert_usable_in_live_plan(all_entries(), context='a live plan')


def test_gate_passes_once_entries_are_verified():
    verified = {
        'id': 'x',
        'provenance': provenance(
            Origin.MODEL_GENERATED, 'checked', status=VerificationStatus.VERIFIED
        ),
    }
    assert unverified_entries([verified]) == []
    assert_usable_in_live_plan([verified])  # must not raise


def test_client_supplied_data_does_not_require_verification():
    """Real execution records from the client are the trusted source, not model output."""
    entry = {'id': 'real', 'provenance': provenance(Origin.CLIENT_SUPPLIED, 'from client')}
    assert unverified_entries([entry]) == []


# ----------------------------------------------------------------- domain content sanity


def test_decision_point_library_covers_the_documented_forks():
    """All eight forks in DOMAIN_KNOWLEDGE.md §6 must be present, or the simulator cannot stop."""
    ids = {e['id'] for e in load_library('decision_points')['entries']}
    assert ids == {
        'dp.delivery_mode', 'dp.ofe', 'dp.grid_position', 'dp.tier_topology',
        'dp.greenfield_brownfield', 'dp.phasing', 'dp.long_lead_unconfirmed',
        'dp.city_pathway_unconfirmed',
    }
    for entry in load_library('decision_points')['entries']:
        assert entry['blocking'] is True
        assert entry['why_stuck'] and entry['options']


def test_tier_1_safety_items_block_export_and_need_hse_signoff():
    entries = load_library('safety_register')['entries']
    assert len(entries) == 5
    for entry in entries:
        assert entry['hitl_tier'] == 'tier_1'
        assert entry['blocks_export'] is True
        assert entry['requires_signoff_role'] == 'hse'


def test_city_pathway_encodes_the_hard_statutory_constraints():
    """Energisation cannot precede CEIG; occupancy cannot precede the final fire NOC."""
    entries = {e['id']: e for e in load_city_pathway('navi_mumbai')['entries']}
    ceig = entries['path.nm.ceig_energisation']
    assert 'energisation' in ceig['blocks']
    assert 'CEIG' in ceig['authority']
    fire = entries['path.nm.fire_noc_final']
    assert 'occupancy' in fire['blocks']


def test_fragnet_logic_links_reference_real_activities():
    """A logic link to a non-existent activity would produce a broken schedule silently."""
    for frag in load_library('fragnets')['entries']:
        activity_ids = {a['id'] for a in frag['activities']}
        for link in frag['logic']:
            assert link['from'] in activity_ids, f'{frag["id"]}: bad from {link["from"]}'
            assert link['to'] in activity_ids, f'{frag["id"]}: bad to {link["to"]}'
            assert link['type'] in {'FS', 'SS', 'FF', 'SF'}
            assert isinstance(link['lag'], int)


def test_fragnet_material_links_point_at_real_lead_time_entries():
    lead_ids = {e['id'] for e in load_library('equipment_lead_times')['entries']}
    for frag in load_library('fragnets')['entries']:
        for link in frag.get('material_links', []):
            assert link['requires_delivery_of'] in lead_ids, frag['id']


def test_commissioning_ist_is_marked_tier_1_safety():
    """DOMAIN_KNOWLEDGE.md §7: IST under load is safety-critical and blocks export."""
    frag = next(f for f in load_library('fragnets')['entries']
                if f['id'] == 'frag.commissioning.ladder')
    ist = next(a for a in frag['activities'] if 'integrated systems test' in a['name'].lower())
    assert ist['safety_flag'] is True
    assert ist['hitl_tier'] == 'tier_1'


# ------------------------------------------------------------------------- reporting / DB


def test_verification_report_counts_the_invented_entries():
    report = verification_report()
    assert report['total_entries'] > 0
    assert report['invented_count'] > 0
    assert report['all_verified'] is False
    assert report['unverified_count'] == report['total_entries']
    assert 'INVENTED BY THE MODEL' in report['summary']
    assert Origin.MODEL_GENERATED.value in report['by_origin']


def test_register_libraries_records_the_version(session):
    result = register_libraries(session)
    assert result['library_version'] == library_version()

    versions = session.execute(select(LibraryVersion)).scalars().all()
    assert len(versions) == 1
    names = {row.name for row in session.execute(select(Library)).scalars().all()}
    assert set(REQUIRED_LIBRARIES).issubset(names)
    assert any(n.startswith('city_pathways/') for n in names)
    assert any('INVENTED' in w for w in result['warnings'])


def test_register_libraries_is_idempotent(session):
    register_libraries(session)
    register_libraries(session)
    assert len(session.execute(select(LibraryVersion)).scalars().all()) == 1
    rows = session.execute(select(Library)).scalars().all()
    assert len(rows) == len({(r.name, r.version) for r in rows})


def test_compliance_registers_are_created_unapproved(session):
    result = register_compliance_registers(session)
    assert result['created'] > 0
    assert result['approved'] == 0
    registers = session.execute(select(ComplianceRegister)).scalars().all()
    assert registers
    assert all(r.approved is False for r in registers)
    assert 'UNAPPROVED' in result['warning']


def test_library_files_are_valid_json_with_required_fields():
    from backend.app.libraries import DATA_DIR

    for path in sorted(DATA_DIR.rglob('*.json')):
        data = json.loads(path.read_text(encoding='utf-8'))
        assert 'library' in data and 'library_version' in data and 'entries' in data, path.name
        assert 'provenance_summary' in data, f'{path.name} must state what was invented'
