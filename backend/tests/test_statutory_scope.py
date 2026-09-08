"""A statutory approval can be issued per building, or once for the campus.

An audit noticed that CEIG appeared hall-specific while the Occupancy Certificate and Fire NOC did
not, and asked whether that was regulation or a gap. It was neither: every approval is a single
campus-wide instance, and CEIG merely wore a hall label from the 4D display fallback (fixed
separately). The real finding was that the pathway library had NO WAY TO SAY which it is - nine
fields across eight approvals, none of them about scope - and the engine instanced every entry
exactly once with no zone handling at all.

So this adds the mechanism, not the answers. `scope: per_zone` instances an approval per data
hall; `scope: campus`, the default, keeps one. WHETHER a given approval is issued per building is
a regulatory question for a compliance team, and no entry is marked `per_zone` here - a plan that
claims per-hall Occupancy Certificates because an engineer guessed would be worse than one that
admits it does not know.

What this does buy: when the compliance answer arrives it is a one-field library change reviewed
in admin, not an engine change. And the default is now explicit rather than accidental.
"""

from __future__ import annotations

import pytest

from backend.app.engine.assemble import assemble
from backend.app.libraries import load_city_pathway, load_library
from backend.app.llm_stub import StubAdapter
from backend.app.simulator import DecisionAnswer, Simulator

BRIEF = {
    'project_name': 'Statutory scope', 'city': 'Navi Mumbai', 'tier': 'III',
    'it_load_mw': 36.0, 'redundancy_topology': 'N+1', 'site_context': 'greenfield',
    'data_hall_count': 4, 'hall_handover_interval_days': 90,
}


def run(pathway_override=None):
    simulator = Simulator(dict(BRIEF), run_id='statutory-scope', adapter=StubAdapter())
    if pathway_override is not None:
        simulator.libraries = dict(simulator.libraries or {}, city_pathway=pathway_override)
    for _ in range(50):
        list(simulator.run())
        if not simulator.is_halted:
            break
        for fork in sorted(simulator.state.pending_decisions):
            payload = simulator.state.pending_decisions[fork]
            simulator.answer(DecisionAnswer(
                decision_point_id=fork, answer=(payload.get('options') or ['Confirmed'])[0],
            ))
    return simulator.output()


def statutory(output):
    return [a for a in output.activities if str(a.get('id', '')).startswith('stat.')]


def test_no_approval_is_declared_per_zone_without_a_compliance_answer():
    """THE LIBRARY MUST NOT GUESS. Whether an Occupancy Certificate is issued per building is a
    question for a compliance team, and the honest default is the one that claims less."""
    entries = load_city_pathway('navi_mumbai')['entries']
    declared = [e['id'] for e in entries if e.get('scope') == 'per_zone']
    assert not declared, (
        f'{declared} are marked per-zone. That is a regulatory claim; it needs a compliance '
        'sign-off recorded in provenance, not an engineer deciding it looks right'
    )


def test_a_campus_approval_is_instanced_once():
    """Today's behaviour, now by declaration rather than by the engine having no alternative."""
    output = run()
    for activity in statutory(output):
        assert not activity['id'].endswith(('.z01', '.z02', '.z03', '.z04')), activity['id']
    ids = [a['id'] for a in statutory(output)]
    assert len(ids) == len(set(ids)), f'a campus approval was instanced more than once: {ids}'


def test_an_approval_declared_per_zone_is_instanced_per_hall():
    """THE MECHANISM. Declared per-zone, the approval appears once per data hall, each carrying
    its own zone - so a phased campus can show hall 2 waiting on hall 2's certificate."""
    entries = [dict(e) for e in load_city_pathway('navi_mumbai')['entries']]
    for entry in entries:
        if entry['id'] == 'path.nm.occupancy_certificate':
            entry['scope'] = 'per_zone'

    output = run(pathway_override=entries)
    occupancy = [a for a in statutory(output) if 'occupancy' in a['id']]
    halls = {
        a['zone_id'] for a in output.activities
        if a.get('zone_id') and 'data-hall' in a['zone_id'] and not a.get('zone_inferred')
    }
    assert len(halls) > 1, 'this plan has one hall, so per-zone instancing cannot be observed'
    assert len(occupancy) == len(halls), (
        f'{len(occupancy)} occupancy certificates for {len(halls)} halls: '
        f'{[a["id"] for a in occupancy]}'
    )
    zones = {a.get('zone_id') for a in occupancy}
    assert zones == halls, f'certificates are not one per hall: {sorted(z or "-" for z in zones)}'
    assert all(not a.get('zone_inferred') for a in occupancy), (
        'a per-zone approval must be genuinely instanced, not drawn into a zone'
    )


def test_the_other_approvals_are_unaffected_by_one_being_per_zone():
    """A scope declaration is per entry. Marking one must not multiply the rest."""
    entries = [dict(e) for e in load_city_pathway('navi_mumbai')['entries']]
    for entry in entries:
        if entry['id'] == 'path.nm.occupancy_certificate':
            entry['scope'] = 'per_zone'

    output = run(pathway_override=entries)
    fire = [a for a in statutory(output) if 'fire-noc' in a['id']]
    assert len(fire) == 1, f'the Fire NOC multiplied too: {[a["id"] for a in fire]}'


def test_an_unknown_scope_is_treated_as_campus_and_not_silently_multiplied():
    """A typo in the library must not invent four certificates. It claims less, not more."""
    entries = [dict(e) for e in load_city_pathway('navi_mumbai')['entries']]
    for entry in entries:
        if entry['id'] == 'path.nm.occupancy_certificate':
            entry['scope'] = 'per-building'  # not a value the engine knows

    output = run(pathway_override=entries)
    occupancy = [a for a in statutory(output) if 'occupancy' in a['id']]
    assert len(occupancy) == 1, f'an unrecognised scope multiplied the approval: {occupancy}'
