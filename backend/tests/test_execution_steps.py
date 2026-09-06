"""Tier 4 pilot: three deliverables in the power train, decomposed into execution steps.

"Transformer placement and alignment, 8 days" is a DELIVERABLE, not something a foreman can
sequence. It hides a rigging study, an offload, a levelling, a grouting, an oil check and a set
of pre-energisation tests - different trades, different plant, and one of them is the only part
that actually needs the transformer to have arrived.

The discipline that makes this tier safe rather than merely bigger is that the step durations SUM
EXACTLY to the parent's. Decomposition adds detail; it does not re-estimate. If the steps summed
to anything else, every date downstream would have moved on the strength of numbers I invented,
and that movement would be indistinguishable from a real re-forecast.

Measured on the Navi Mumbai brief, and this is the property the tests below pin:

    Tier 2 baseline                        RFS 863,  256 activities
    steps added, links still on parents    RFS 863,  382 activities   <- decomposition alone
    steps + links on the consuming step    RFS 840,  382 activities   <- the delivery fix

So all 23 days come from re-pointing the deliveries, none from the step durations.
"""

from __future__ import annotations

import pytest

from backend.app.libraries import load_library
from backend.app.llm_stub import StubAdapter
from backend.app.simulator import DecisionAnswer, Simulator

BRIEF = {
    'project_name': 'Execution steps', 'city': 'Navi Mumbai', 'tier': 'III',
    'it_load_mw': 20.0, 'redundancy_topology': 'N+1', 'site_context': 'greenfield',
}
FRAGNET = 'frag.mep.power_train'


@pytest.fixture(scope='module')
def plan():
    simulator = Simulator(BRIEF, run_id='steps', adapter=StubAdapter())
    for _ in range(40):
        list(simulator.run())
        if not simulator.is_halted:
            break
        for fork in sorted(simulator.state.pending_decisions):
            simulator.answer(DecisionAnswer(decision_point_id=fork, answer='Proceed'))
    assert not simulator.is_halted, 'the walk never completed'
    return simulator.output().model_dump()


def fragnet():
    return next(f for f in load_library('fragnets')['entries'] if f['id'] == FRAGNET)


def decomposed():
    return [a for a in fragnet()['activities'] if a.get('steps')]


def instances(plan, needle):
    return [a for a in plan['activities'] if needle in a['name']]


# ------------------------------------------------------------------ the sum rule

def test_the_steps_sum_to_the_deliverable_they_decompose():
    """The rule the whole tier rests on. Break it and the plan silently re-forecasts."""
    activities = decomposed()
    assert len(activities) >= 3, 'the pilot decomposed fewer than three deliverables'
    for activity in activities:
        total = sum(int(s['duration_days']) for s in activity['steps'])
        assert total == activity['duration_days'], (
            f'{activity["id"]}: steps sum to {total} days, the deliverable is '
            f'{activity["duration_days"]} - decomposition has become a re-estimate'
        )


def test_a_deliverable_takes_exactly_as_long_as_it_did(plan):
    """Not just in the library: in the assembled, scheduled plan.

    The steps run in series with no lag, so the elapsed span from the first step starting to the
    last one finishing must equal the duration the deliverable always had.
    """
    for activity in decomposed():
        rows = [a for a in plan['activities']
                if a['name'].startswith(activity['name'] + ':')
                and a.get('zone_id', '').endswith('.01')]
        assert rows, f'{activity["id"]} produced no steps in the plan'
        span = max(r['finish_day'] for r in rows) - min(r['start_day'] for r in rows)
        assert span == activity['duration_days'], (
            f'{activity["id"]} now spans {span} days, not {activity["duration_days"]}'
        )


def test_the_deliverable_itself_is_gone_from_the_plan(plan):
    """The parent is REPLACED, not kept beside its steps - keeping both double-counts it."""
    for activity in decomposed():
        bare = [a for a in plan['activities']
                if a['name'].split(' - ')[0] == activity['name']]
        assert not bare, (
            f'{activity["id"]} appears as itself as well as its steps, so its duration is '
            'counted twice'
        )


# ------------------------------------------------------------------ the sequencing

def test_steps_run_in_series_inside_their_deliverable(plan):
    for activity in decomposed():
        rows = sorted(
            (a for a in plan['activities']
             if a['name'].startswith(activity['name'] + ':')
             and a.get('zone_id', '').endswith('room.01')),
            key=lambda a: a['start_day'],
        )
        for earlier, later in zip(rows, rows[1:]):
            assert later['start_day'] >= earlier['finish_day'], (
                f'{later["name"]} starts before {earlier["name"]} finishes'
            )


def test_a_finish_to_start_link_joins_the_last_step_to_the_first(plan):
    """Once c20 is six steps, "c20 -> c30 FS" has to mean LAST of c20 to FIRST of c30.

    Attaching both ends to the first step turns a finish-to-start into a start-to-start and
    quietly overlaps two activities that must not overlap - a mistake that reads as a schedule
    improvement.
    """
    by_id = {a['id']: a for a in plan['activities']}
    link = next(l for l in fragnet()['logic'] if l['from'] == 'c10' and l['type'] == 'FS')
    assert link  # c10 -> c20, into a decomposed activity

    first_steps = [a for a in plan['activities'] if 'Rigging study' in a['name']]
    assert first_steps, 'the first step of c20 is not in the plan'
    for step in first_steps:
        preds = [by_id[p['id']] for p in step['predecessors'] if p['id'] in by_id]
        assert any('plinth and pad preparation' in p['name'] for p in preds), (
            'the first step of the transformer placement does not follow the plinth'
        )


def test_a_hold_on_the_deliverable_lands_after_its_last_step(plan):
    """A pre-energisation inspection is not passed halfway through the installation."""
    by_id = {a['id']: a for a in plan['activities']}
    holds = [a for a in plan['activities'] if 'Pre-energisation inspection' in a['name']]
    assert holds, 'the switchgear hold point vanished'
    for hold in holds:
        preds = [by_id[p['id']] for p in hold['predecessors'] if p['id'] in by_id]
        assert any('final dressing' in p['name'] for p in preds), (
            f'{hold["name"]} does not follow the last step of the switchgear install'
        )


def test_each_hold_belongs_to_its_own_room(plan):
    """Left to the zone fallback, every room's inspection was filed in room 01."""
    holds = [a for a in plan['activities'] if 'Pre-energisation inspection' in a['name']]
    zones = {h['zone_id'] for h in holds}
    assert len(zones) == len(holds), f'{len(holds)} holds share {len(zones)} zones'


# ------------------------------------------------------------------ the delivery fix

def test_a_delivery_gates_only_the_step_that_needs_the_plant(plan):
    """The point of the pilot, and the only thing in it that moves a date.

    A rigging study for a transformer can be done while the transformer is still in the factory.
    The offload cannot. Before this the whole eight-day activity waited on delivery.
    """
    by_id = {a['id']: a for a in plan['activities']}
    arrivals = [a for a in plan['activities']
                if a['id'].startswith('gate.delivery-lead-transformer-hv')]
    assert arrivals

    offloads = [a for a in plan['activities'] if 'Offload from trailer' in a['name']]
    studies = [a for a in plan['activities'] if 'Rigging study' in a['name']]
    assert offloads and studies

    # The offload waits for an arrival...
    for offload in offloads:
        preds = [p['id'] for p in offload['predecessors']]
        assert any(p.startswith('gate.delivery-lead-transformer-hv') for p in preds), (
            'the offload does not wait for the transformer to arrive'
        )

    # ...and the rigging study does not.
    for study in studies:
        preds = [p['id'] for p in study['predecessors']]
        assert not any(p.startswith('gate.delivery-lead-transformer-hv') for p in preds), (
            'the rigging study still waits on delivery, so the decomposition bought nothing'
        )

    # And it shows in the dates, ROOM BY ROOM. Comparing against the earliest arrival across
    # the whole campus would be the wrong test: room 1's transformer lands long before its
    # plinth is ready, so delivery is not what binds it. The later rooms are where the plant is
    # the constraint, and those are where preparation running ahead of it is worth something.
    arrival_for = {a['zone_id']: a['start_day'] for a in arrivals}
    ahead = [
        study for study in studies
        if study['start_day'] < arrival_for.get(study['zone_id'], -1)
    ]
    assert ahead, (
        'in no room does the rigging study start before that room\'s own transformer arrives, '
        'so the re-pointing bought nothing anywhere'
    )


# ------------------------------------------------------------------ the flagging

def test_every_invented_step_is_flagged_the_way_the_library_flags_estimates():
    provenance = fragnet().get('provenance') or {}
    assert provenance.get('verification_status') == 'unverified'
    assert 'INVENTED FOR REVIEW' in (provenance.get('note') or '')
    # And the note has to be honest about the one thing that DID move dates.
    assert 'move' in (provenance.get('note') or '').lower()


def test_the_steps_carry_the_capped_confidence_of_their_source(plan):
    """A step is exactly as trustworthy as the fragnet it was invented into, and no more."""
    steps = [a for a in plan['activities'] if a.get('source_fragnet') == FRAGNET
             and ':' in a['name'] and a['type'] == 'task']
    assert steps
    for step in steps:
        assert step['confidence'] <= 0.5, (
            f'{step["name"]} claims confidence {step["confidence"]} on unverified library data'
        )
        assert FRAGNET in (step.get('unverified_dependencies') or [])


def test_steps_are_instanced_per_room_like_the_work_they_belong_to(plan):
    """Decomposition and zone multiplication have to compose, not fight."""
    offloads = [a for a in plan['activities'] if 'Offload from trailer' in a['name']]
    zones = {a['zone_id'] for a in offloads}
    assert len(zones) > 1, 'the steps were not instanced per electrical room'
    assert len(zones) == len(offloads), 'two steps share a room'

# ----------------------------------------------- the endpoint rule, on its own terms
#
# The three deliverables this pilot decomposed are joined to their neighbours by START-to-start
# links, so nothing in the power train exercises a finish-to-start link running OUT of a
# decomposed activity - a mutation that collapsed every link onto the first step changed no
# behaviour and failed no test. The rule still has to be right, because the next fragnet to be
# decomposed will exercise it. So it is tested here directly, on a fragnet built for the purpose.

from backend.app.engine import assemble  # noqa: E402
from backend.app.schemas import PackageSelection, StageReasoning  # noqa: E402


def _synthetic(link_type, lag=0):
    """Two decomposed activities, joined by one link of the given type."""
    fragnet = {
        'id': 'frag.test.pair', 'name': 'Pair', 'stage': 'substructure', 'dept': 'civil',
        'materials': [], 'gates': [], 'hold_points': [], 'material_links': [],
        'activities': [
            {'id': 'a10', 'name': 'First deliverable', 'duration_days': 6, 'calendar': '6day',
             'steps': [{'id': 's10', 'name': 'A one', 'duration_days': 3},
                       {'id': 's20', 'name': 'A two', 'duration_days': 3}]},
            {'id': 'a20', 'name': 'Second deliverable', 'duration_days': 4, 'calendar': '6day',
             'steps': [{'id': 's10', 'name': 'B one', 'duration_days': 2},
                       {'id': 's20', 'name': 'B two', 'duration_days': 2}]},
        ],
        'logic': [{'from': 'a10', 'to': 'a20', 'type': link_type, 'lag': lag}],
        'provenance': {'origin': 'industry_estimate', 'verification_status': 'unverified'},
    }
    selection = PackageSelection(
        fragnet_id='frag.test.pair', why='test', confidence=0.9, effective_confidence=0.5,
        sources=['corpus:1#0'], unverified_dependencies=['frag.test.pair'],
    )
    result = assemble(
        [StageReasoning(stage='substructure', packages=[selection])],
        BRIEF, libraries={'fragnets': [fragnet]},
    )
    joins = [
        (e.from_id.split('.')[-1], e.to_id.split('.')[-1])
        for e in result.edges
        if e.kind == 'fragnet' and e.from_id.split('.')[-1].startswith('a10')
        and e.to_id.split('.')[-1].startswith('a20')
    ]
    return joins


@pytest.mark.parametrize('link_type,expected', [
    ('FS', ('a10-s20', 'a20-s10')),   # last finishes, then the next starts
    ('SS', ('a10-s10', 'a20-s10')),   # both starts
    ('FF', ('a10-s20', 'a20-s20')),   # both finishes
    ('SF', ('a10-s10', 'a20-s20')),   # the first starts, the second may then finish
])
def test_a_link_attaches_to_the_end_its_type_names(link_type, expected):
    """Attaching both ends to the first step turns a finish-to-start into a start-to-start and
    overlaps two activities that must not overlap - a mistake that reads as an improvement."""
    assert _synthetic(link_type) == [expected]


def test_a_delivery_named_against_a_deliverable_gates_its_first_step():
    """Defensive, and worth pinning because nothing in the shipped library reaches it.

    The power train's material links were re-pointed at the exact steps that consume the plant.
    A library author who instead names the whole deliverable must still get the sane answer -
    the plant is needed when the work STARTS - rather than a delivery that gates the finish and
    lets the offload happen before the transformer lands.
    """
    fragnet = {
        'id': 'frag.test.delivery', 'name': 'Delivery', 'stage': 'mep_power', 'dept': 'mep',
        'materials': [], 'gates': [], 'hold_points': [],
        'material_links': [{'activity': 'a10', 'requires_delivery_of': 'lead.transformer_hv'}],
        'activities': [
            {'id': 'a10', 'name': 'Whole deliverable', 'duration_days': 4, 'calendar': '6day',
             'steps': [{'id': 's10', 'name': 'Step one', 'duration_days': 2},
                       {'id': 's20', 'name': 'Step two', 'duration_days': 2}]},
        ],
        'logic': [],
        'provenance': {'origin': 'industry_estimate', 'verification_status': 'unverified'},
    }
    selection = PackageSelection(
        fragnet_id='frag.test.delivery', why='test', confidence=0.9, effective_confidence=0.5,
        sources=['corpus:1#0'], unverified_dependencies=['frag.test.delivery'],
    )
    result = assemble(
        [StageReasoning(stage='mep_power', packages=[selection])],
        BRIEF, libraries={'fragnets': [fragnet]},
    )
    gated = sorted({
        e.to_id.split('.')[-1] for e in result.edges
        if e.kind == 'delivery' and e.from_id.startswith('gate.delivery-lead-transformer-hv')
    })
    assert gated == ['a10-s10'], f'the delivery gates {gated}, not the first step'
