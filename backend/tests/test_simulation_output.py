"""Golden test for the assembled SimulationOutput (Task 9).

SIMULATION_AND_REASONING.md §7: one simulation, many projections. `flow` drives the 2D view,
`zones` plus each activity's stage/zone_id drive the 3D/4D model, `activities` export to P6, and
`reasoning_trail` powers hover-to-explain. This test pins that one object.

The golden snapshot is compared in full. Regenerate deliberately, never automatically:

    python -m backend.tests.golden_support --regenerate

and read the diff before committing it — an automatic refresh would let a regression walk into
the fixture and look like a pass.
"""

from __future__ import annotations

import json

import pytest

from backend.app.schemas import SimulationOutput
from backend.tests.golden_support import (
    EXPECTED_FORKS,
    GOLDEN_PATH,
    SEED_ANSWERS,
    build_golden_output,
    load_golden,
    seed_brief,
)

#: The output contract from SIMULATION_AND_REASONING.md §7, verbatim.
SPEC_KEYS = {
    'project_meta', 'questions', 'decisions', 'flow', 'statutory_pathway', 'equipment_counts',
    'long_lead_register', 'activities', 'commissioning', 'zones', 'reasoning_trail', 'quality',
    'flags',
}

#: The node kinds the 2D view must be able to tell apart (VISUALIZATION_SPEC.md §1).
SPEC_NODE_KINDS = {
    'stage', 'work_package', 'activity', 'milestone', 'compliance_gate', 'quality_hold',
    'decision_point',
}


@pytest.fixture(scope='module')
def built():
    return build_golden_output()


@pytest.fixture(scope='module')
def output(built):
    return built[1]


@pytest.fixture(scope='module')
def simulator(built):
    return built[0]


# ------------------------------------------------------------------------ the golden


def test_output_matches_the_golden_snapshot(output):
    """Full-object compare. Assembly is deterministic, so any drift is a real change."""
    assert GOLDEN_PATH.is_file(), (
        'golden fixture missing; regenerate with '
        'python -m backend.tests.golden_support --regenerate'
    )
    assert json.loads(output.model_dump_json()) == load_golden()


def test_output_is_deterministic():
    first = build_golden_output()[1].model_dump_json()
    second = build_golden_output()[1].model_dump_json()
    assert first == second


# --------------------------------------------------------------------- structure


def test_output_carries_every_key_the_spec_names(output):
    assert set(output.model_dump()) == SPEC_KEYS


def test_output_validates_as_the_declared_schema(output):
    """Round-trips through the schema, so the golden cannot drift from the type contract."""
    assert SimulationOutput.model_validate(output.model_dump()) == output


def test_project_meta_carries_the_brief_and_the_versions(output):
    meta = output.project_meta
    brief = seed_brief()
    assert meta['city'] == brief['city']
    assert meta['tier'] == brief['tier']
    assert meta['it_load_mw'] == brief['it_load_mw']
    # A plan must be traceable to the library and prompt that produced it.
    assert meta['library_version'] and meta['prompt_version'] and meta['corpus_version']


def test_flow_distinguishes_every_node_kind_the_2d_view_needs(output):
    kinds = {n['kind'] for n in output.flow['nodes']}
    assert kinds == SPEC_NODE_KINDS


def test_flow_nodes_are_uniquely_identified(output):
    ids = [n['id'] for n in output.flow['nodes']]
    assert len(ids) == len(set(ids))


def test_flow_edges_reference_real_nodes(output):
    """A dangling edge would draw a line to nowhere in the 2D view."""
    node_ids = {n['id'] for n in output.flow['nodes']}
    for edge in output.flow['edges']:
        assert edge['from'] in node_ids, f'edge from unknown node {edge["from"]}'
        assert edge['to'] in node_ids, f'edge to unknown node {edge["to"]}'


def test_activities_are_grouped_under_their_package_and_stage(output):
    """VISUALIZATION_SPEC.md §1: grouping/collapse by stage, WBS or department."""
    node_ids = {n['id'] for n in output.flow['nodes']}
    for node in output.flow['nodes']:
        if node['kind'] == 'activity':
            assert node['parent'] in node_ids


def test_zones_and_activities_share_a_zone_vocabulary(output):
    """The 3D/4D model is driven by zones plus each activity's zone_id."""
    zone_ids = {z['id'] for z in output.zones}
    used = {a['zone_id'] for a in output.activities if a.get('zone_id')}
    assert used, 'no activity carries a zone, so nothing would build in 3D'
    assert used <= zone_ids, f'activities reference unknown zones: {used - zone_ids}'


def test_commissioning_ladder_runs_l1_to_l5_with_ist(output):
    levels = [c['level'] for c in output.commissioning if c['level']]
    assert levels == ['L1', 'L2', 'L3', 'L4', 'L5']
    ist = [c for c in output.commissioning if c['is_IST']]
    assert len(ist) == 1 and ist[0]['blocks_export'] is True


def test_every_activity_has_a_trail_entry(output):
    """§5: every node carries a trail entry, so the simulation is cross-verifiable."""
    refs = {t['ref_id'] for t in output.reasoning_trail}
    for activity in output.activities:
        if activity['type'] == 'task':
            assert activity['id'] in refs, f'{activity["id"]} has no reasoning trail'


def test_trail_entries_cite_sources(output):
    for entry in output.reasoning_trail:
        assert entry['sources'], f'{entry["ref_id"]} cites nothing'


# ------------------------------------------------------- the forks the seed brief fires


def test_the_expected_decision_points_fire_for_the_seed_brief(output):
    """The seed brief has exactly the ambiguities these forks exist to catch.

    gensets are owner-furnished (dp.ofe); the HT substation is on our scope and energisation is
    the client's critical concern (dp.grid_position); no lead time is confirmed by a PO
    (dp.long_lead_unconfirmed); the topology drives equipment counts (dp.tier_topology).
    """
    fired = {d.id for d in output.decisions}
    expected = {f for forks in EXPECTED_FORKS.values() for f in forks}
    assert fired == expected


def test_every_fired_fork_is_recorded_with_its_answer(output):
    for decision in output.decisions:
        assert decision.question, f'{decision.id} recorded without its question'
        assert decision.answer == SEED_ANSWERS[decision.id]
        assert decision.impact, f'{decision.id} recorded without its impact'


def test_resolved_forks_stay_visible_in_the_flow(output):
    """VISUALIZATION_SPEC.md §4: resolved decisions remain visible, with the answer."""
    decision_nodes = {n['id']: n for n in output.flow['nodes'] if n['kind'] == 'decision_point'}
    assert decision_nodes

    for decision in output.decisions:
        node = decision_nodes[f'decision.{decision.id}']
        assert node['status'] == 'resolved'
        assert node['answer'] == decision.answer


def test_decisions_are_in_the_reasoning_trail(output):
    """Every downstream element should be able to cite the decision it depended on (§4)."""
    refs = {t['ref_id'] for t in output.reasoning_trail}
    for decision in output.decisions:
        assert f'decision.{decision.id}' in refs


def test_no_forks_remain_open_once_answered(output, simulator):
    assert output.quality['open_decision_count'] == 0
    assert simulator.is_halted is False


def test_a_halted_run_still_projects_a_usable_output():
    """A partial simulation is legitimate to render, and must say it is unfinished."""
    from backend.app.simulator import Simulator
    import backend.app.simulator.runner as runner_module
    from backend.tests.golden_support import GOLDEN_WALK, GoldenReasoner

    original = runner_module.reason_stage
    runner_module.reason_stage = GoldenReasoner()
    try:
        simulator = Simulator(seed_brief(), run_id='halted', stages=GOLDEN_WALK)
        list(simulator.run())
        output = simulator.output()
    finally:
        runner_module.reason_stage = original

    assert simulator.is_halted is True
    assert output.quality['open_decision_count'] > 0
    assert output.quality['governance_complete'] is False
    open_nodes = [n for n in output.flow['nodes']
                  if n['kind'] == 'decision_point' and n['status'] == 'open']
    assert open_nodes
    assert all(n['why_stuck'] for n in open_nodes), 'an open fork must say why it is stuck'


# ------------------------------------------------------------------------ governance


def test_governance_is_not_complete_while_tier_1_is_unsigned(output):
    """CLAUDE.md rule 5: Tier-1 safety blocks export until a human signs off."""
    assert output.quality['export_blocked'] is True
    assert output.quality['tier_1_count'] > 0
    assert output.quality['governance_complete'] is False
    assert 'sign-off' in output.quality['export_block_reason']


def test_unverified_library_data_is_surfaced_not_buried(output):
    """The plan must say it rests on stand-in data rather than presenting it as researched."""
    assert output.quality['unverified_dependencies']
    assert output.quality['tier_2_count'] > 0
    tier_2 = [a for a in output.activities if a['hitl_tier'] == 'tier_2']
    assert tier_2
    assert all(a['unverified_dependencies'] for a in tier_2)


def test_long_lead_register_carries_provenance(output):
    """Lead times drive RFS, so their provenance must travel with them."""
    assert output.long_lead_register
    for item in output.long_lead_register:
        assert item['provenance']['verification_status'] == 'unverified'
        assert 'NOT A PROJECT ACTUAL' in item['provenance']['warning']


def test_statutory_pathway_is_not_marked_approved(output):
    """ADMIN_SPEC.md §4: a register must be approved in admin before driving a live plan."""
    assert output.statutory_pathway
    for gate in output.statutory_pathway:
        assert gate['compliance_approved'] is False
        assert gate['authority']


def test_dcma_summary_declares_what_it_did_not_check(output):
    """Reporting an unrun check as passing would be worse than reporting it as absent."""
    dcma = output.quality['dcma_summary']
    assert dcma['activity_count'] > 0
    assert 'float' in dcma['checks_not_run']
    assert 'critical_path_length_index' in dcma['checks_not_run']
    assert 'Partial' in dcma['note']
