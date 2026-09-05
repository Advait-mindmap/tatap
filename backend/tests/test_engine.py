"""The deterministic engine (Task 7).

Three things these tests exist to hold:

1. **No LLM in this layer, and assembly is deterministic** — same reasoning in, byte-identical
   activities out, regardless of input ordering.
2. **The cross-stage gates work with thin data and get better with more** — the IFC and delivery
   gates constrain the programme today, when `frag.design.*` and `frag.procurement.*` do not
   exist, and anchor to real predecessors the moment they do, with no code change.
3. **Governance survives assembly** — department, compliance gates, safety holds, Tier-2
   unverified dependencies and the capped confidence all reach the instanced activity. Assembly
   is the easiest place for these to fall off silently.
"""

from __future__ import annotations

import json

import pytest

from backend.app.engine import (
    CROSS_STAGE_GATES,
    assemble,
    delivery_gate_id,
    generate_zones,
    match_safety_rules,
    material_link_index,
)
from backend.app.libraries import load_library
from backend.app.schemas import GateSelection, PackageSelection, StageReasoning

BRIEF = {
    'project_name': 'POC DC',
    'city': 'Navi Mumbai',
    'tier': 'III',
    'it_load_mw': 20.0,
    'redundancy_topology': 'N+1',
    'delivery_mode_by_discipline': {
        'civil': 'self-perform', 'electrical': 'turnkey', 'mechanical': 'turnkey',
        'gensets': 'owner-furnished',
    },
}


def pkg(fragnet_id, *, confidence=0.9, effective=0.5, unverified=None):
    return PackageSelection(
        fragnet_id=fragnet_id, why=f'{fragnet_id} applies to this stage',
        confidence=confidence, effective_confidence=effective,
        sources=['corpus:1#0'],
        unverified_dependencies=[fragnet_id] if unverified is None else unverified,
    )


def gate(gate_id_):
    return GateSelection(
        gate_id=gate_id_, why='statutory gate', confidence=0.8, effective_confidence=0.5,
        unverified_dependencies=[gate_id_],
    )


def reasoning():
    """A representative multi-stage reasoning result."""
    return [
        StageReasoning(stage='design', packages=[], library_version='v1', corpus_version='v1',
                       prompt_version='v1'),
        StageReasoning(stage='procurement', packages=[], library_version='v1'),
        StageReasoning(stage='substructure', packages=[pkg('frag.substructure.raft')]),
        StageReasoning(stage='mep_power', packages=[pkg('frag.mep.power_train')],
                       gates=[gate('path.nm.peso_hsd')]),
        StageReasoning(stage='mep_cooling', packages=[pkg('frag.mep.cooling')]),
        StageReasoning(stage='commissioning', packages=[pkg('frag.commissioning.ladder')]),
    ]


@pytest.fixture
def result():
    return assemble(reasoning(), BRIEF)


# ------------------------------------------------------------------- purity & determinism


ENGINE_MODULES = ('assemble', 'gates', 'zones', 'ids')


def _engine_source(name: str) -> str:
    """Engine module source with comments stripped, so prose does not trip the checks."""
    import importlib
    import pathlib as _pathlib

    module = importlib.import_module(f'backend.app.engine.{name}')
    source = _pathlib.Path(module.__file__).read_text(encoding='utf-8')
    return '\n'.join(
        line for line in source.splitlines() if not line.lstrip().startswith('#')
    )


def test_engine_makes_no_llm_calls():
    """CLAUDE.md rule 2: the engine instances, it does not reason.

    Checks the source rather than mocking a provider, so a call added later is caught even if no
    test ever exercises that path.
    """
    for name in ENGINE_MODULES:
        code = _engine_source(name)
        for forbidden in ('get_adapter', 'Base44Adapter', 'app.llm', 'import httpx'):
            assert forbidden not in code, f'engine.{name} references {forbidden}'


def test_engine_uses_no_clock_or_random_source():
    """The mechanism behind byte-identity: no clock, no RNG, no allocated ids."""
    for name in ENGINE_MODULES:
        code = _engine_source(name)
        for forbidden in ('datetime.now', 'utcnow', 'time.time', 'random.', 'uuid'):
            assert forbidden not in code, f'engine.{name} uses {forbidden}'


def test_assembly_is_byte_identical_on_re_run():
    """SIMULATION_AND_REASONING.md §8: the engine's assembly is deterministic either way."""
    first = assemble(reasoning(), BRIEF).model_dump_json()
    second = assemble(reasoning(), BRIEF).model_dump_json()
    assert first == second
    assert json.loads(first) == json.loads(second)


def test_assembly_is_independent_of_input_stage_order():
    """A caller must not be able to perturb the output by reordering its input."""
    forward = assemble(reasoning(), BRIEF).model_dump_json()
    reversed_ = assemble(list(reversed(reasoning())), BRIEF).model_dump_json()
    assert forward == reversed_


def test_activity_ids_are_derived_not_allocated(result):
    """An allocated (counter or uuid) id would break identity across runs."""
    from backend.app.engine import activity_id

    for activity in result.activities:
        # Fragnet-instanced work only. Statutory approvals are real tasks with real durations,
        # but they come from the city pathway rather than a fragnet, so there is no
        # (stage, fragnet, spec) triple to derive an id from - theirs is derived from the
        # pathway id instead, which is just as stable across runs.
        if activity.type == 'task' and activity.source_fragnet:
            assert activity.id == activity_id(
                activity.stage, activity.source_fragnet, activity.id.rsplit('.', 1)[-1]
            )


# ------------------------------------------------------------------- fragnet instancing


def test_every_selected_fragnet_activity_is_instanced(result):
    frag = next(f for f in load_library('fragnets')['entries']
                if f['id'] == 'frag.mep.power_train')
    instanced = [a for a in result.activities if a.source_fragnet == 'frag.mep.power_train'
                 and a.type == 'task']
    assert len(instanced) == len(frag['activities'])
    assert {a.name for a in instanced} == {s['name'] for s in frag['activities']}


def test_durations_come_from_the_library_not_invented(result):
    frag = next(f for f in load_library('fragnets')['entries'] if f['id'] == 'frag.mep.cooling')
    expected = {s['name']: s['duration_days'] for s in frag['activities']}
    for activity in result.activities:
        if activity.source_fragnet == 'frag.mep.cooling' and activity.type == 'task':
            assert activity.duration_days == expected[activity.name]


def test_fragnet_logic_is_wired_with_type_and_lag(result):
    """SS with a 10-day lag must survive assembly as SS with a 10-day lag."""
    frag = next(f for f in load_library('fragnets')['entries']
                if f['id'] == 'frag.mep.power_train')
    link = next(l for l in frag['logic'] if l['type'] == 'SS' and l['lag'] > 0)
    edge = next(
        e for e in result.edges
        if e.kind == 'fragnet' and e.from_id.endswith(link['from'])
        and e.to_id.endswith(link['to'])
    )
    assert edge.type == link['type']
    assert edge.lag == link['lag']


def test_wbs_ids_are_hierarchical_and_stage_ordered(result):
    tasks = [a for a in result.activities if a.type == 'task']
    assert all(len(a.wbs_id.split('.')) == 3 for a in tasks)
    substructure = next(a for a in tasks if a.stage == 'substructure')
    commissioning = next(a for a in tasks if a.stage == 'commissioning')
    assert substructure.wbs_id < commissioning.wbs_id


# --------------------------------------------------------------- cross-stage gate machinery


def test_ifc_gate_exists_and_gates_procurement(result):
    """Engineering drives procurement: you cannot order to an unfixed specification."""
    rule = next(r for r in CROSS_STAGE_GATES if r.id == 'ifc_issued')
    ident = 'gate.ifc-issued'
    assert any(a.id == ident and a.type == 'gate' for a in result.activities)
    assert rule.producer_stage == 'design' and 'procurement' in rule.consumer_stages


def test_ifc_gate_is_emitted_even_though_design_instanced_nothing(result):
    """The constraint is carried whether or not the producing stage produced anything.

    The warning must name WHICH of the two reasons applies, because they have different
    remedies. This fixture selects no design package even though frag.design.engineering now
    exists, so the honest reading is "walked, covered, produced nothing" - not the library gap
    the message used to assert unconditionally.
    """
    incoming = [e for e in result.edges if e.to_id == 'gate.ifc-issued']
    assert incoming == [], 'design instanced nothing, so the gate has no predecessor yet'
    warning = next(w for w in result.warnings if 'gate.ifc-issued' in w)
    assert 'the library covers it' in warning
    assert 'does not exist yet' not in warning, (
        'the library does cover design now; saying otherwise sends the reader to fix the wrong '
        'thing'
    )


def test_ifc_gate_anchors_automatically_once_a_design_fragnet_exists():
    """The requirement: works the moment frag.design.* exists, with no re-architecting.

    Nothing below changes engine code — a fragnet is added to the library and selected.
    """
    design_fragnet = {
        'id': 'frag.design.basis', 'name': 'Basis of design', 'stage': 'design', 'dept': 'design',
        'materials': [], 'gates': [],
        'activities': [
            {'id': 'x10', 'name': 'Basis of design', 'duration_days': 20, 'calendar': '5day'},
            {'id': 'x20', 'name': 'IFC drawings issued', 'duration_days': 30, 'calendar': '5day'},
        ],
        'logic': [{'from': 'x10', 'to': 'x20', 'type': 'FS', 'lag': 0}],
        'hold_points': [],
        'provenance': {'origin': 'industry_estimate', 'verification_status': 'unverified'},
    }
    libs = {'fragnets': load_library('fragnets')['entries'] + [design_fragnet]}
    stages = reasoning()
    stages[0] = StageReasoning(stage='design', packages=[pkg('frag.design.basis')])

    res = assemble(stages, BRIEF, libraries=libs)
    incoming = [e for e in res.edges if e.to_id == 'gate.ifc-issued']

    assert incoming, 'the gate should now anchor to the design activities'
    assert all(e.kind == 'cross_stage_gate' for e in incoming)
    assert not any('does not exist yet' in w for w in res.warnings)


def test_delivery_gates_tie_construction_to_delivery(result):
    """DOMAIN_KNOWLEDGE.md §4: front-load long lead, tie construction to delivery."""
    ident = delivery_gate_id('lead.transformer_hv')
    assert any(a.id == ident and a.type == 'milestone' for a in result.activities)

    gated = [e.to_id for e in result.edges if e.from_id == ident and e.kind == 'delivery']
    assert gated, 'the delivery milestone must gate the installing activity'
    target = next(a for a in result.activities if a.id == gated[0])
    assert 'transformer' in target.name.lower()
    assert {'id': ident, 'type': 'FS', 'lag': 0, 'kind': 'delivery'} in target.predecessors


def test_delivery_gates_are_derived_from_library_material_links():
    """Declared as data, so a fragnet added later is picked up with no code change."""
    index = material_link_index(load_library('fragnets')['entries'])
    assert index['lead.transformer_hv'] == [('frag.mep.power_train', 'c20')]
    assert 'lead.chiller' in index


def test_no_delivery_gate_without_a_consuming_activity():
    """Gate only what is actually instanced; an unconsumed lead adds no phantom milestone."""
    res = assemble([StageReasoning(stage='substructure', packages=[pkg('frag.substructure.raft')])],
                   BRIEF)
    assert not [a for a in res.activities if a.id.startswith('gate.delivery')]


def test_delivery_gate_anchors_to_procurement_when_it_has_activities():
    procurement_fragnet = {
        'id': 'frag.procurement.long_lead', 'name': 'Long-lead procurement',
        'stage': 'procurement', 'dept': 'procurement', 'materials': [], 'gates': [],
        'activities': [
            {'id': 'p10', 'name': 'RFQ and tender', 'duration_days': 30, 'calendar': '5day'},
            {'id': 'p20', 'name': 'Award and PO', 'duration_days': 15, 'calendar': '5day'},
        ],
        'logic': [{'from': 'p10', 'to': 'p20', 'type': 'FS', 'lag': 0}],
        'hold_points': [],
        'provenance': {'origin': 'industry_estimate', 'verification_status': 'unverified'},
    }
    libs = {'fragnets': load_library('fragnets')['entries'] + [procurement_fragnet]}
    stages = reasoning()
    stages[1] = StageReasoning(stage='procurement', packages=[pkg('frag.procurement.long_lead')])

    res = assemble(stages, BRIEF, libraries=libs)
    ident = delivery_gate_id('lead.transformer_hv')
    incoming = [e for e in res.edges if e.to_id == ident and e.kind == 'delivery']
    assert incoming, 'delivery milestone should now follow the procurement activities'


# ------------------------------------------------------------------------- governance


def test_department_codes_are_carried(result):
    for activity in result.activities:
        if activity.type == 'task':
            assert activity.dept_code, f'{activity.id} lost its department'


def test_delivery_mode_reaches_the_activities_it_governs(result):
    """The per-discipline answer to dp.delivery_mode must not stop at the brief."""
    # source_fragnet filters out the statutory approvals that now sit in these stages. A PESO
    # licence is not self-performed or subcontracted - it is applied for - so delivery mode is
    # meaningless for it, and asserting one would be asserting a fiction.
    electrical = [a for a in result.activities
                  if a.stage == 'mep_power' and a.type == 'task' and a.source_fragnet]
    assert electrical and all(a.delivery_mode == 'turnkey' for a in electrical)
    civil = [a for a in result.activities
             if a.stage == 'substructure' and a.type == 'task' and a.source_fragnet]
    assert civil and all(a.delivery_mode == 'self-perform' for a in civil)


def test_compliance_gates_are_carried_onto_activities(result):
    mep = [a for a in result.activities
           if a.stage == 'mep_power' and a.type == 'task' and a.source_fragnet]
    assert all('path.nm.peso_hsd' in a.compliance_gates for a in mep)


def test_quality_hold_points_are_instanced_as_nodes(result):
    holds = [a for a in result.activities if a.type == 'hold_point']
    assert holds
    for hold in holds:
        assert hold.duration_days == 0
        assert hold.predecessors, 'a hold point must follow the activity it holds'


def test_tier_2_unverified_dependencies_and_capped_confidence_survive(result):
    """The anti-laundering guarantee must not be lost in assembly."""
    mep = [a for a in result.activities if a.source_fragnet == 'frag.mep.power_train']
    assert mep
    for activity in mep:
        assert activity.unverified_dependencies == ['frag.mep.power_train']
        assert activity.confidence == 0.5, 'must be the capped value, not the 0.9 claimed'
        assert activity.hitl_tier in ('tier_1', 'tier_2')
    assert result.governance['unverified_dependencies']
    assert any('capped value' in w for w in result.warnings)


def test_verified_selection_is_not_marked_tier_2():
    res = assemble(
        [StageReasoning(stage='substructure',
                        packages=[pkg('frag.substructure.raft', effective=0.9, unverified=[])])],
        BRIEF,
    )
    tasks = [a for a in res.activities if a.type == 'task']
    assert all(a.unverified_dependencies == [] for a in tasks)
    assert all(a.hitl_tier == 'tier_3' for a in tasks)


def test_trail_entry_per_assembled_activity(result):
    """SIMULATION_AND_REASONING.md §5: every element carries a trail entry."""
    tasks = {a.id for a in result.activities if a.type == 'task'}
    refs = {t.ref_id for t in result.trail}
    assert tasks <= refs
    for entry in result.trail:
        assert entry.sources and entry.why


# ------------------------------------------------------------- safety and export blocking


def test_ist_is_tier_1_and_blocks_export(result):
    ist = next(a for a in result.activities
               if 'integrated systems test' in a.name.lower() and a.type == 'task')
    assert ist.safety_flag is True
    assert ist.hitl_tier == 'tier_1'
    assert ist.blocks_export is True
    assert result.export_blocked is True
    assert 'sign-off' in result.governance['export_block_reason']


def test_safety_matching_is_by_keyword_not_substring():
    """Library patterns are descriptions, not the activity names they must catch."""
    safety = load_library('safety_register')['entries']
    matched = match_safety_rules('L5 integrated systems test (IST)', safety)
    assert [m['id'] for m in matched] == ['safety.ist_under_load']
    assert match_safety_rules('Excavation to formation level', safety) == []


def test_export_is_not_blocked_without_tier_1_work():
    res = assemble([StageReasoning(stage='substructure',
                                   packages=[pkg('frag.substructure.raft')])], BRIEF)
    assert res.export_blocked is False
    assert res.governance['tier_1_count'] == 0


# --------------------------------------------------------------- Cx ladder and zones


def test_commissioning_ladder_is_ordered_and_marks_ist(result):
    levels = [c['level'] for c in result.commissioning if c['level']]
    assert levels == ['L1', 'L2', 'L3', 'L4', 'L5']
    ist = [c for c in result.commissioning if c['is_IST']]
    assert len(ist) == 1 and ist[0]['blocks_export'] is True


def test_zones_are_sized_from_the_load(result):
    halls = [z for z in result.zones if z['kind'] == 'data_hall']
    # 20 MW at 3.0 MW/hall -> 7 halls.
    assert len(halls) == 7
    assert all(z['unverified_dependencies'] for z in halls), 'sizing rests on estimate data'


def test_zone_generation_is_deterministic():
    rules = load_library('tier_rules')['entries']
    assert generate_zones(BRIEF, rules) == generate_zones(BRIEF, rules)


def test_zones_carry_the_stage_that_first_builds_them(result):
    kinds = {z['kind']: z['stage'] for z in result.zones}
    assert kinds['data_hall'] == 'superstructure'
    assert kinds['cooling_plant'] == 'mep_cooling'


def test_versions_are_recorded(result):
    assert result.library_version


DESIGN_FRAGNET = {
    'id': 'frag.design.basis', 'name': 'Basis of design', 'stage': 'design', 'dept': 'design',
    'materials': [], 'gates': [],
    'activities': [
        {'id': 'x10', 'name': 'Basis of design', 'duration_days': 20, 'calendar': '5day'},
        {'id': 'x20', 'name': 'IFC drawings issued', 'duration_days': 30, 'calendar': '5day'},
    ],
    'logic': [{'from': 'x10', 'to': 'x20', 'type': 'FS', 'lag': 0}],
    'hold_points': [],
    'provenance': {'origin': 'industry_estimate', 'verification_status': 'unverified'},
}

PROCUREMENT_FRAGNET = {
    'id': 'frag.procurement.long_lead', 'name': 'Long-lead procurement', 'stage': 'procurement',
    'dept': 'procurement', 'materials': [], 'gates': [],
    'activities': [
        {'id': 'p10', 'name': 'RFQ and tender', 'duration_days': 30, 'calendar': '5day'},
        {'id': 'p20', 'name': 'Award and PO', 'duration_days': 15, 'calendar': '5day'},
    ],
    'logic': [{'from': 'p10', 'to': 'p20', 'type': 'FS', 'lag': 0}],
    'hold_points': [],
    'provenance': {'origin': 'industry_estimate', 'verification_status': 'unverified'},
}


def test_ifc_gate_actually_constrains_procurement_end_to_end():
    """The headline requirement, proved as a chain rather than as a node.

    design activity -> IFC gate -> procurement activity, with no engine change: the two fragnets
    are simply added to the library and selected.
    """
    libs = {'fragnets': load_library('fragnets')['entries']
            + [DESIGN_FRAGNET, PROCUREMENT_FRAGNET]}
    stages = reasoning()
    stages[0] = StageReasoning(stage='design', packages=[pkg('frag.design.basis')])
    stages[1] = StageReasoning(stage='procurement', packages=[pkg('frag.procurement.long_lead')])

    res = assemble(stages, BRIEF, libraries=libs)
    ident = 'gate.ifc-issued'

    into_gate = [e.from_id for e in res.edges if e.to_id == ident]
    out_of_gate = [e.to_id for e in res.edges if e.from_id == ident]

    assert into_gate, 'design activities must precede the IFC gate'
    assert out_of_gate, 'the IFC gate must precede procurement activities'
    assert all(i.startswith('design.') for i in into_gate)
    assert all(o.startswith('procurement.') for o in out_of_gate)

    # And the constraint reaches the activity itself, not just the edge list.
    procurement_activity = next(a for a in res.activities if a.id == out_of_gate[0])
    assert any(p['id'] == ident and p['kind'] == 'cross_stage_gate'
               for p in procurement_activity.predecessors)
    assert not any('does not exist yet' in w for w in res.warnings)


def test_full_chain_design_to_procurement_to_delivery_to_install():
    """The whole cross-stage spine: design -> procurement -> delivery -> MEP install."""
    libs = {'fragnets': load_library('fragnets')['entries']
            + [DESIGN_FRAGNET, PROCUREMENT_FRAGNET]}
    stages = reasoning()
    stages[0] = StageReasoning(stage='design', packages=[pkg('frag.design.basis')])
    stages[1] = StageReasoning(stage='procurement', packages=[pkg('frag.procurement.long_lead')])

    res = assemble(stages, BRIEF, libraries=libs)
    edges = {(e.from_id, e.to_id) for e in res.edges}
    delivery = delivery_gate_id('lead.transformer_hv')

    assert any(f.startswith('design.') and t == 'gate.ifc-issued' for f, t in edges)
    assert any(f == 'gate.ifc-issued' and t.startswith('procurement.') for f, t in edges)
    assert any(f.startswith('procurement.') and t == delivery for f, t in edges)
    assert any(f == delivery and t.startswith('mep_power.') for f, t in edges)
