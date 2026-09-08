"""Three audit bugs: the brief's own numbers, and a gate that pointed at nothing.

All three shared a shape - the plan looked complete and was quietly built on something other
than what the brief said, or on nothing at all.

1. THE HALL COUNT WAS COMPUTED, NOT READ. A brief stating "four data halls" had that number
   discarded and replaced by `ceil(it_load_mw / mw_per_hall)` using unverified sizing data, so
   fit-out, fire and power were multiplied across the wrong number of halls.
2. PHASED HANDOVER WAS IGNORED. "remaining halls following at approximately six-month intervals"
   drove nothing; halls completed within days of each other.
3. AN UNANCHORED GATE SAT ON DAY ZERO. A gate whose producer stage instanced nothing had no
   predecessors, so it landed on day one and RELEASED everything downstream - inverting the
   constraint it exists to carry while looking like an achieved milestone in the export.

The hall count and the interval are asserted against TWO different briefs, because a fix that
hardcodes this project's four halls or six months passes a single-brief test perfectly.
"""

from __future__ import annotations

import pytest

from backend.app.engine import assemble
from backend.app.engine.gates import CROSS_STAGE_GATES, cross_stage_gate_id
from backend.app.engine.ids import zone_index_of
from backend.app.engine.zones import generate_zones
from backend.app.intake import extract_brief
from backend.app.intake.prompt import parse_interval_days
from backend.app.libraries import load_library
from backend.app.llm_stub import StubAdapter
from backend.app.schemas import PackageSelection, StageReasoning
from backend.app.simulator import DecisionAnswer, Simulator

#: Two briefs that differ in BOTH numbers under test, so neither can be satisfied by a constant.
BRIEF_A = """We are bidding a 30 MW Tier IV data centre in Chennai on a greenfield parcel.
Topology is 2N. The campus is four data halls.
Hall 1 targeted for readiness-for-service in Q2 2028, remaining halls following at
approximately six-month intervals through to Q4 2029."""

BRIEF_B = """We are bidding a 30 MW Tier IV data centre in Navi Mumbai on a greenfield parcel.
Topology is 2N. The campus is six data halls.
Hall 1 hands over first, with the remaining halls following at three-month intervals."""

#: (text, halls the brief states, interval in days it states)
CASES = [(BRIEF_A, 4, 180), (BRIEF_B, 6, 90)]


def plan_for(text, run_id):
    brief = extract_brief(text, adapter=StubAdapter()).brief.model_dump()
    simulator = Simulator(brief, run_id=run_id, adapter=StubAdapter())
    for _ in range(40):
        list(simulator.run())
        if not simulator.is_halted:
            break
        for fork in sorted(simulator.state.pending_decisions):
            simulator.answer(DecisionAnswer(decision_point_id=fork, answer='Proceed'))
    assert not simulator.is_halted, 'the walk never completed'
    return brief, simulator.output().model_dump()


@pytest.fixture(scope='module')
def plans():
    return {
        'A': plan_for(BRIEF_A, 'phasing-a'),
        'B': plan_for(BRIEF_B, 'phasing-b'),
    }


def hall_completion(output):
    """Last finish day per hall, over work genuinely INSTANCED for that hall.

    The filter matters. Campus-wide activities and stage-completion gates land in
    `zone.data-hall.01` through the zone fallback, and counting them makes hall 1 look like the
    last hall to finish - which is how this measurement lied twice while the fix was being
    built. An id carrying a zone index is the only reliable marker of per-hall work.
    """
    done = {}
    for activity in output['activities']:
        zone = activity.get('zone_id') or ''
        if (zone.startswith('zone.data-hall.') and activity['type'] == 'task'
                and zone_index_of(activity['id'])):
            done[zone] = max(done.get(zone, 0), activity['finish_day'])
    return [done[zone] for zone in sorted(done)]


# ------------------------------------------------------------- 1. the stated hall count

@pytest.mark.parametrize('key,text,halls,interval', [
    ('A', BRIEF_A, 4, 180), ('B', BRIEF_B, 6, 90),
])
def test_the_hall_count_comes_from_the_brief(plans, key, text, halls, interval):
    brief, output = plans[key]
    assert brief['data_hall_count'] == halls

    built = {a['zone_id'] for a in output['activities']
             if (a.get('zone_id') or '').startswith('zone.data-hall.')}
    assert len(built) == halls, f'the brief states {halls} halls; the plan built {len(built)}'


def test_the_two_briefs_disagree_about_everything_under_test():
    """Guards the guard. If both cases wanted the same numbers, a hardcoded fix would pass."""
    counts = {halls for _, halls, _ in CASES}
    intervals = {interval for _, _, interval in CASES}
    assert len(counts) == len(CASES), 'both briefs state the same hall count'
    assert len(intervals) == len(CASES), 'both briefs state the same interval'


def test_the_stated_count_beats_the_formula(plans):
    """The formula would give a different answer for these briefs, which is the point.

    30 MW over the library's per-hall figure is not four halls and not six. If the formula ever
    happened to agree, this test would pass while proving nothing - so it asserts they differ.
    """
    rules = load_library('tier_rules')['entries']
    for key, (brief, _) in plans.items():
        without = dict(brief)
        without.pop('data_hall_count')
        formula_halls = len([z for z in generate_zones(without, rules)
                             if z['kind'] == 'data_hall'])
        stated_halls = len([z for z in generate_zones(brief, rules)
                            if z['kind'] == 'data_hall'])
        assert stated_halls == brief['data_hall_count']
        assert formula_halls != stated_halls, (
            f'brief {key}: the formula also gives {formula_halls} halls, so this proves nothing'
        )


def test_a_brief_that_states_no_count_still_falls_back_to_the_formula():
    """The formula is the fallback, not the enemy. Removing it would break every brief that
    does not happen to name a hall count."""
    rules = load_library('tier_rules')['entries']
    silent = {'city': 'Chennai', 'tier': 'IV', 'it_load_mw': 30.0,
              'redundancy_topology': '2N'}
    halls = [z for z in generate_zones(silent, rules) if z['kind'] == 'data_hall']
    assert halls, 'a brief with no stated count produced no halls at all'
    assert halls[0]['derived_from']['hall_count_source'] == 'mw_per_hall_formula'


def test_the_plan_records_which_way_the_count_was_decided(plans):
    rules = load_library('tier_rules')['entries']
    brief, _ = plans['A']
    hall = next(z for z in generate_zones(brief, rules) if z['kind'] == 'data_hall')
    assert hall['derived_from']['hall_count_source'] == 'brief'
    assert hall['derived_from']['stated_data_hall_count'] == 4


def test_the_count_is_extracted_with_a_quote_like_every_other_field():
    result = extract_brief(BRIEF_A, adapter=StubAdapter())
    provenance = result.field_provenance['data_hall_count']
    assert provenance.quote.strip(), 'the hall count was accepted with no citation'
    assert provenance.quote.strip() in BRIEF_A, 'the citation is not verbatim in the brief'
    assert 'data halls' in provenance.quote


# ------------------------------------------------------------- 2. the stated interval

@pytest.mark.parametrize('phrase,days', [
    ('approximately six-month intervals', 180),
    ('three-month intervals', 90),
    ('quarterly', 91),
    ('every 12 weeks', 84),
    ('90 days', 90),
    ('two months', 60),
    ('when it is ready', None),
    ('', None),
])
def test_interval_language_parses_to_days(phrase, days):
    assert parse_interval_days(phrase) == days


def test_the_month_convention_is_the_documented_one():
    """Thirty days, not 30.44 and not the calendar month the phrase lands in.

    The input says "approximately"; carrying more precision than the source has would be false
    precision. This pins the choice so it is a decision rather than an accident.
    """
    assert parse_interval_days('six-month') == 180
    assert parse_interval_days('one month') == 30
    assert parse_interval_days('twelve months') == 360


@pytest.mark.parametrize('key,text,halls,interval', [
    ('A', BRIEF_A, 4, 180), ('B', BRIEF_B, 6, 90),
])
def test_halls_complete_at_the_stated_interval(plans, key, text, halls, interval):
    """The bug as measured: halls finished within a ten-day spread instead of months apart."""
    brief, output = plans[key]
    assert brief['hall_handover_interval_days'] == interval

    days = hall_completion(output)
    assert len(days) == halls
    gaps = [later - earlier for earlier, later in zip(days, days[1:])]
    assert gaps, 'only one hall completed, so there is no interval to check'

    # EVERY GAP is the stated interval. Not "roughly" - the engine schedules to a number, and the
    # number came from the brief.
    #
    # This briefly read `gaps[1:]` while a campus-wide prerequisite compressed the first gap.
    # Commissioning had become per-hall, so a hall's completion was its own commissioning
    # finishing - and `gate.power-installed` released campus-wide, holding hall 1 to day 448 while
    # its own fit-out gate finished on 413. Halls completed 145, 180, 180 days apart.
    #
    # That gate now releases per hall, because electrical rooms are zone-instanced and hall N
    # needs room N energised rather than the whole campus. The first gap opened back to 180 and
    # the assertion is exact again. Recording it because the weakened version was correct about
    # the behaviour at the time and would have quietly accepted the defect forever.
    assert set(gaps) == {interval}, (
        f'brief {key}: halls complete {gaps} days apart, not {interval}'
    )


def test_a_brief_with_no_stated_interval_does_not_invent_one(plans):
    """Absent phasing must mean the halls run as construction allows, not a default stagger."""
    silent = """We are bidding a 30 MW Tier IV data centre in Chennai on a greenfield parcel.
Topology is 2N. The campus is four data halls."""
    brief = extract_brief(silent, adapter=StubAdapter()).brief.model_dump()
    assert brief.get('hall_handover_interval_days') is None

    simulator = Simulator(brief, run_id='phasing-silent', adapter=StubAdapter())
    for _ in range(40):
        list(simulator.run())
        if not simulator.is_halted:
            break
        for fork in sorted(simulator.state.pending_decisions):
            simulator.answer(DecisionAnswer(decision_point_id=fork, answer='Proceed'))
    days = hall_completion(simulator.output().model_dump())
    gaps = [later - earlier for earlier, later in zip(days, days[1:])]
    assert max(gaps or [0]) < 180, (
        f'no interval was stated, yet the halls are staggered {gaps} days apart'
    )


def test_the_interval_is_carried_on_the_edge_that_applies_it(plans):
    """It is an interpretation of the brief's wording, so it says so where it acts."""
    _, output = plans['A']
    gates = [a for a in output['activities']
             if a['id'].startswith('gate.envelope-weathertight.z')]
    assert len(gates) == 4
    reasons = [p for gate in gates for p in (gate.get('predecessors') or [])
               if p['id'].endswith('.z01') and p['type'] == 'SS' and p['lag']]
    assert reasons, 'no phased-handover edge is present'
    assert {p['lag'] for p in reasons} == {180, 360, 540}


# ------------------------------------------------------------- 3. the unanchored gate

def _walked_but_empty(producer_stage, consumer_stages):
    """Walk a stage with nothing selected, and populate its consumers."""
    by_stage = {}
    for entry in load_library('fragnets')['entries']:
        by_stage.setdefault(entry['stage'], entry['id'])
    stages = [StageReasoning(stage=producer_stage, packages=[])]
    for consumer in consumer_stages:
        if consumer in by_stage and consumer != producer_stage:
            stages.append(StageReasoning(stage=consumer, packages=[PackageSelection(
                fragnet_id=by_stage[consumer], why='t', confidence=0.9,
                effective_confidence=0.5, sources=['corpus:1#0'],
                unverified_dependencies=[by_stage[consumer]],
            )]))
    if len(stages) == 1:
        return None
    return assemble(stages, {'project_name': 'x', 'city': 'Navi Mumbai', 'tier': 'III',
                             'it_load_mw': 20.0, 'redundancy_topology': 'N+1'})


def test_the_commissioning_gate_is_anchored_on_a_normal_run():
    """The reported symptom, on a plan where commissioning does instance work."""
    simulator = Simulator(
        {'project_name': 'x', 'city': 'Navi Mumbai', 'tier': 'III', 'it_load_mw': 20.0,
         'redundancy_topology': 'N+1', 'site_context': 'greenfield'},
        run_id='gate-anchor', adapter=StubAdapter(),
    )
    for _ in range(40):
        list(simulator.run())
        if not simulator.is_halted:
            break
        for fork in sorted(simulator.state.pending_decisions):
            simulator.answer(DecisionAnswer(decision_point_id=fork, answer='Proceed'))
    output = simulator.output().model_dump()
    gate = next(a for a in output['activities'] if a['id'] == 'gate.commissioning-complete')
    assert gate['predecessors'], 'the commissioning gate has no predecessors'
    assert gate['start_day'] > 0, 'the commissioning gate is on day zero'


def test_no_gate_or_milestone_sits_on_day_zero_unnoticed():
    """The general form. A milestone nothing points into cannot push its date forward, and it
    releases its consumers immediately - so it must at least be impossible to export quietly."""
    simulator = Simulator(
        {'project_name': 'x', 'city': 'Navi Mumbai', 'tier': 'III', 'it_load_mw': 20.0,
         'redundancy_topology': 'N+1', 'site_context': 'greenfield'},
        run_id='gate-audit', adapter=StubAdapter(),
    )
    for _ in range(40):
        list(simulator.run())
        if not simulator.is_halted:
            break
        for fork in sorted(simulator.state.pending_decisions):
            simulator.answer(DecisionAnswer(decision_point_id=fork, answer='Proceed'))
    for activity in simulator.output().model_dump()['activities']:
        if activity['type'] in ('gate', 'milestone') and not activity['predecessors']:
            assert activity['blocks_export'], (
                f'{activity["id"]} has no predecessors and does not block export'
            )


@pytest.mark.parametrize('rule', [r for r in CROSS_STAGE_GATES], ids=lambda r: r.id)
def test_an_unanchored_gate_blocks_export(rule):
    """Every gate in the table, not just the one that was reported.

    Eleven of the twelve can be emitted unanchored, and `ifc_construction` unanchored releases
    445 activities - far more damaging than the commissioning gate that surfaced the bug.
    """
    result = _walked_but_empty(rule.producer_stage, rule.consumer_stages)
    if result is None:
        pytest.skip(f'{rule.id} has no consumer stage with a fragnet to populate')
    ident = cross_stage_gate_id(rule)
    gate = next((a for a in result.activities if a.id == ident), None)
    if gate is None or gate.predecessors:
        pytest.skip(f'{rule.id} could not be made unanchored on this library')

    assert gate.blocks_export, f'{ident} is unanchored and exports silently'
    assert gate.hitl_tier == 'tier_1'
    assert 'UNANCHORED' in gate.name, 'the name reads as an achieved milestone'


def test_an_anchored_gate_does_not_block_export():
    """The guard must not fire on healthy plans, or every export needs a signature."""
    result = _walked_but_empty('substructure', ('superstructure',))
    assert result is not None
    blocking = [a for a in result.activities if a.type == 'gate' and a.blocks_export
                and a.predecessors]
    assert not blocking, f'anchored gates are blocking export: {[a.id for a in blocking]}'
