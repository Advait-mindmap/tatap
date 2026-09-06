"""Tier 2: work that repeats per hall is planned per hall.

A fragnet describes the work in ONE data hall or ONE electrical room. Instancing it once for a
campus that has eight of them was the largest single source of thinness in the plan: eight halls
of fit-out arrived as one 30-day bar, every hall completed on the same day in the 4D model, and
there was no activity a per-hall delivery or a per-hall release could attach to.

These tests cover the three things that had to be true together, because any one of them alone
produces a plan that looks richer and is not:

1. The work is repeated per zone - and work that genuinely happens once is NOT.
2. The logic stays inside its zone. Joining every instance of one end to every instance of the
   other is the failure that hides: the activity count is right, the dates look plausible, and
   eight parallel fit-outs have quietly become one serial one.
3. The releases and deliveries are per zone too. Multiplying the activities while leaving one
   gate to release all of them just draws eight bars that all start on the same day.
"""

from __future__ import annotations

import collections

import pytest

from backend.app.engine.ids import zone_index_of
from backend.app.libraries import load_library
from backend.app.llm_stub import StubAdapter
from backend.app.simulator import DecisionAnswer, Simulator


#: 20 MW at 2.5 MW a hall is eight halls, so this brief exercises the multiplication.
MULTI_HALL = {
    'project_name': 'Zone multiplication', 'city': 'Navi Mumbai', 'tier': 'III',
    'it_load_mw': 20.0, 'redundancy_topology': 'N+1', 'site_context': 'greenfield',
}

#: Small enough to produce a single data hall, for the degenerate case.
ONE_HALL = dict(MULTI_HALL, it_load_mw=2.0)


class Plan:
    """One completed run, in the shape the engine hands back.

    Driven through the real simulator rather than assembled from a hand-built reasoning list,
    because the stages this tier touches - envelope releasing fit-out, procurement anchoring the
    deliveries - only exist together in a full walk. A partial reasoning fixture would leave the
    delivery gates unanchored on day zero and quietly make half of these assertions vacuous.
    """

    def __init__(self, brief, run_id):
        simulator = Simulator(brief, run_id=run_id, adapter=StubAdapter())
        for _ in range(40):
            list(simulator.run())
            if not simulator.is_halted:
                break
            for fork in sorted(simulator.state.pending_decisions):
                simulator.answer(DecisionAnswer(decision_point_id=fork, answer='Proceed'))
        assert not simulator.is_halted, 'the walk never completed'
        output = simulator.output().model_dump()
        self.activities = [_Row(a) for a in output['activities']]
        self.edges = [_Row(e) for e in output.get('edges') or _edges_from(output)]
        self.zones = output.get('zones') or []


class _Row(dict):
    __getattr__ = dict.get


def _edges_from(output):
    """The output publishes logic as each activity's predecessors; read the edges back off it."""
    return [
        {'from_id': pred['id'], 'to_id': activity['id'],
         'type': pred.get('type'), 'lag': pred.get('lag'), 'kind': pred.get('kind')}
        for activity in output['activities']
        for pred in (activity.get('predecessors') or [])
    ]


@pytest.fixture(scope='module')
def result():
    return Plan(MULTI_HALL, 'zone-multi')


@pytest.fixture(scope='module')
def single():
    return Plan(ONE_HALL, 'zone-single')


def zones_in(result, kind):
    prefix = 'zone.' + kind.replace('_', '-') + '.'
    return sorted({a.zone_id for a in result.activities if (a.zone_id or '').startswith(prefix)})


def library(fragnet_id):
    return next(f for f in load_library('fragnets')['entries'] if f['id'] == fragnet_id)


# ------------------------------------------------------------------ 1. the multiplication

@pytest.mark.parametrize('fragnet_id,kind', [
    ('frag.fit_out.cabling', 'data_hall'),
    ('frag.fire_bms.detection_suppression', 'data_hall'),
    ('frag.mep.power_train', 'electrical_room'),
])
def test_a_zone_bearing_fragnet_is_instanced_once_per_zone(result, fragnet_id, kind):
    frag = library(fragnet_id)
    assert frag.get('zone_kind') == kind, f'{fragnet_id} is not marked zone-bearing'

    zones = zones_in(result, kind)
    assert len(zones) > 1, f'this brief produced {len(zones)} {kind} zones, so this proves nothing'

    instanced = [a for a in result.activities
                 if a.source_fragnet == fragnet_id and a.type == 'task']
    for spec in frag['activities']:
        matching = [a for a in instanced if a.name.split(' - ')[0] == spec['name']]
        if spec.get('zone_scope') == 'project':
            assert len(matching) == 1, (
                f'{spec["id"]} is project-wide and was instanced {len(matching)} times'
            )
        else:
            assert sorted(a.zone_id for a in matching) == zones, (
                f'{spec["id"]} was not instanced in every {kind}'
            )


def test_work_that_happens_once_is_never_multiplied(result):
    """The bulk fuel farm, the BMS head-end, the HV energisation.

    Repeating these would be worse than the thinness it fixes: a plan with seven bulk HSD tanks
    implies seven PESO licences, and a reader has no way to tell that from a real requirement.
    """
    once = {
        'Bulk HSD storage tank and fuel system installation',
        'HV/MV energisation and live electrical testing',
        'Diesel generator set installation and alignment',
        'BMS head-end integration and graphics',
        'Fire alarm cause-and-effect testing',
        'Structured cabling backbone (OS2 / OM4)',
    }
    counts = collections.Counter(
        a.name.split(' - ')[0] for a in result.activities if a.type == 'task'
    )
    for name in sorted(once):
        assert counts[name] == 1, f'"{name}" happens once but appears {counts[name]} times'


def test_every_activity_carries_the_zone_it_belongs_to(result):
    """The 4D model keys on `zone_id`; an instance without one cannot be drawn in its hall."""
    for activity in result.activities:
        index = zone_index_of(activity.id)
        if index:
            assert activity.zone_id, f'{activity.id} is zone-instanced but carries no zone'
            assert activity.zone_id.endswith(f'{index:02d}'), (
                f'{activity.id} says zone {index} but is filed under {activity.zone_id}'
            )


def test_the_zones_are_the_ones_the_model_generates(result):
    """Instancing and the 3D model must not disagree about how many halls exist."""
    generated = {z['id'] for z in result.zones}
    used = {a.zone_id for a in result.activities if a.zone_id}
    assert used <= generated, f'activities reference zones the model never generated: {used - generated}'


def test_a_single_hall_brief_is_not_multiplied(single):
    """The degenerate case has to stay clean, or every small project grows phantom structure."""
    assert len(zones_in(single, 'data_hall')) == 1
    fit_out = [a for a in single.activities
               if a.source_fragnet == 'frag.fit_out.cabling' and a.type == 'task']
    assert len(fit_out) == len(library('frag.fit_out.cabling')['activities'])


# ------------------------------------------------------------------ 2. the logic stays put

def test_fragnet_logic_never_crosses_two_zones(result):
    """The failure that hides. A cartesian join keeps the activity count right, keeps the dates
    plausible, and serialises eight parallel fit-outs into one."""
    for edge in result.edges:
        if edge.kind != 'fragnet':
            continue
        source, target = zone_index_of(edge.from_id), zone_index_of(edge.to_id)
        if source and target:
            assert source == target, (
                f'{edge.from_id} -> {edge.to_id} joins two different zones'
            )


def test_the_halls_are_planned_in_parallel_not_in_series(result):
    """Eight halls one after another is a different project. Their fit-outs must overlap."""
    starts = {}
    for activity in result.activities:
        # The campus-wide backbone has no hall and no place in a per-hall comparison.
        if activity.stage == 'fit_out' and activity.type == 'task' and activity.zone_id:
            starts.setdefault(activity.zone_id, []).append(activity.start_day)
    spans = {zone: (min(days), max(days)) for zone, days in starts.items()}
    assert len(spans) > 1

    ordered = [spans[zone] for zone in sorted(spans)]
    for earlier, later in zip(ordered, ordered[1:]):
        assert later[0] < earlier[1], (
            f'hall fit-outs run end to end ({earlier} then {later}) rather than overlapping'
        )


# ------------------------------------------------------------- 3. per-zone releases

def test_each_hall_is_released_by_its_own_weathertightness_gate(result):
    """Hall 3's fit-out follows hall 3 being clad, not the campus average.

    Before this the single gate released every hall at once, so eight fit-outs began on one day
    - the multiplication was visible in the activity count and absent from the dates.
    """
    gates = sorted((a for a in result.activities
                    if a.id.startswith('gate.envelope-weathertight.z')),
                   key=lambda a: a.id)
    assert len(gates) > 1, 'the weather-tightness release was not staged per hall'

    by_id = {a.id: a for a in result.activities}
    days = [by_id[g.id].start_day for g in gates]
    assert days == sorted(days), f'the halls are released out of order: {days}'
    assert len(set(days)) > 1, 'every hall is released on the same day; the stagger is not real'

    # Each gate reaches its OWN hall's work, and that work starts no earlier than the release.
    #
    # The last gate carries one extra job by design: work that was not instanced per hall waits
    # for the last hall rather than the first, because a system-wide test is not released by one
    # hall being ready. So the assertion is "its own zone, plus unzoned work on the last gate",
    # not "exactly one zone".
    for position, gate in enumerate(gates, start=1):
        released = [e.to_id for e in result.edges
                    if e.from_id == gate.id and e.kind == 'cross_stage_gate']
        assert released, f'{gate.id} releases nothing'
        zones = {by_id[t].zone_id for t in released if t in by_id}
        allowed = {gate.zone_id}
        if position == len(gates):
            allowed |= {z for z in zones if z not in {g.zone_id for g in gates}}
        assert zones <= allowed, (
            f'{gate.id} releases {sorted(zones - allowed)}, which is not its own hall'
        )
        for target in released:
            assert by_id[target].start_day >= by_id[gate.id].start_day


def test_no_gate_is_left_orphaned(result):
    """A gate with no predecessor sits on day zero and constrains nothing; one with no successor
    is a milestone nobody waits for. Both were real bugs while this was being built."""
    incoming = {e.to_id for e in result.edges}
    outgoing = {e.from_id for e in result.edges}
    for activity in result.activities:
        if activity.type != 'gate':
            continue
        assert activity.id in outgoing, f'{activity.id} gates nothing'
        assert activity.id in incoming, (
            f'{activity.id} has no predecessor, so it sits on day 0 and constrains nothing'
        )


# ------------------------------------------------------------- 4. staged deliveries

def test_each_electrical_room_waits_for_its_own_transformer(result):
    """One arrival milestone per room, at the interval the library declares.

    A factory does not ship eight transformers on one day. Gating all eight rooms behind a
    single arrival is what made every room's placement start together.
    """
    base = 'gate.delivery-lead-transformer-hv'
    gates = sorted((a for a in result.activities if a.id.startswith(base + '.z')),
                   key=lambda a: a.id)
    assert len(gates) > 1, 'the transformer still arrives as one batch'

    entry = next(e for e in load_library('equipment_lead_times')['entries']
                 if e['id'] == 'lead.transformer_hv')
    interval = round(entry['delivery_stagger_weeks'] * 7)
    days = [g.start_day for g in gates]
    assert {later - earlier for earlier, later in zip(days, days[1:])} == {interval}

    by_id = {a.id: a for a in result.activities}
    for gate in gates:
        consumers = [e.to_id for e in result.edges
                     if e.from_id == gate.id and e.kind == 'delivery']
        assert consumers, f'{gate.id} gates nothing'
        assert {by_id[c].zone_id for c in consumers} == {gate.zone_id}


def test_an_item_with_no_declared_interval_keeps_one_arrival(result):
    """Absent data must mean the conservative default, not an invented schedule.

    The generator has no `delivery_stagger_weeks` because it is not a per-zone item. If a
    missing field ever started producing a stagger, the plan would be inventing delivery dates.
    """
    entries = {e['id']: e for e in load_library('equipment_lead_times')['entries']}
    assert 'delivery_stagger_weeks' not in entries['lead.generator']
    gates = [a for a in result.activities
             if a.id.startswith('gate.delivery-lead-generator')]
    assert len(gates) == 1, 'an item with no declared interval was split into a schedule'


def test_the_staggered_interval_is_declared_unverified():
    """It is my estimate, not a quoted delivery schedule, and must be flagged as such."""
    for entry in load_library('equipment_lead_times')['entries']:
        if entry.get('delivery_stagger_weeks') is None:
            continue
        provenance = entry.get('provenance') or {}
        assert provenance.get('verification_status') == 'unverified'
        assert 'INVENTED FOR REVIEW' in (provenance.get('note') or '')
