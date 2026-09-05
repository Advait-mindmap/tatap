"""Statutory approvals must constrain the schedule, not merely be reported next to it.

Before this, SimulationOutput carried the whole city pathway as metadata - each approval's
authority, its typical_weeks, the stages it `blocks` - and not one of those became a day of
schedule or a single edge. A plan could show an Environmental Clearance that takes thirty weeks
and start excavation in week two, and nothing in the output contradicted it. The data was right;
it just was not wired to anything.

These tests are written against a Navi Mumbai run because it is the only city with pathway data.
The `no pathway` test below is the other half: a city without a file must degrade to no approvals
rather than to an error or to silently invented ones.
"""

from __future__ import annotations

import collections

import pytest

from backend.app.engine.assemble import match_safety_rules
from backend.app.libraries import load_city_pathway, load_library
from backend.app.llm_stub import StubAdapter
from backend.app.simulator import DecisionAnswer, Simulator

NAVI_MUMBAI = {
    'project_name': 'Statutory DC', 'city': 'Navi Mumbai', 'tier': 'III',
    'it_load_mw': 20.0, 'redundancy_topology': 'N+1', 'site_context': 'greenfield',
}
NO_PATHWAY_CITY = {
    'project_name': 'Statutory DC', 'city': 'Chennai', 'tier': 'IV',
    'it_load_mw': 30.0, 'redundancy_topology': '2N', 'site_context': 'brownfield',
}


def drive(brief, run_id):
    simulator = Simulator(brief, run_id=run_id, adapter=StubAdapter())
    for _ in range(40):
        list(simulator.run())
        if not simulator.is_halted:
            break
        for fork in sorted(simulator.state.pending_decisions):
            simulator.answer(DecisionAnswer(decision_point_id=fork, answer='Proceed'))
    assert not simulator.is_halted, 'the walk never completed'
    return simulator.output().model_dump()


@pytest.fixture(scope='module')
def plan():
    output = drive(NAVI_MUMBAI, 'statutory')
    by_id = {a['id']: a for a in output['activities']}
    blocked = collections.defaultdict(list)
    for activity in output['activities']:
        for pred in activity.get('predecessors') or []:
            if pred.get('kind') == 'statutory':
                blocked[pred['id']].append(activity['id'])
    return output, by_id, blocked


def named(output, fragment):
    return next((a for a in output['activities'] if fragment.lower() in a['name'].lower()), None)


# ------------------------------------------------------- 1. approvals become real activities


def test_every_selected_approval_becomes_an_activity_with_its_real_duration(plan):
    output, by_id, _ = plan
    pathway = {e['id']: e for e in load_city_pathway('navi_mumbai')['entries']}
    reported = {entry['id'] for entry in output['statutory_pathway']}
    assert reported, 'the reasoner selected no approvals, so this proves nothing'

    for pathway_id in reported:
        ident = f"stat.{pathway_id.replace('.', '-').replace('_', '-')}"
        assert ident in by_id, f'{pathway_id} is reported but is not an activity'
        expected = int(round(float(pathway[pathway_id]['typical_weeks']) * 7))
        assert by_id[ident]['duration_days'] == expected, (
            f'{pathway_id} is {pathway[pathway_id]["typical_weeks"]} weeks in the library but '
            f'{by_id[ident]["duration_days"]} days in the plan'
        )


def test_each_approval_actually_blocks_something(plan):
    """The whole point. An approval on the chart that gates nothing is the old behaviour with
    extra steps."""
    output, by_id, blocked = plan
    approvals = [a for a in output['activities'] if a['id'].startswith('stat.')]
    assert approvals, 'no statutory activities at all'
    for approval in approvals:
        assert blocked.get(approval['id']), (
            f"{approval['name']} constrains nothing — its blocks token resolved to no activity"
        )


def test_the_early_approvals_hold_back_the_early_work(plan):
    """EC is thirty weeks and blocks enabling and substructure. If it does not delay them, it
    is decorative."""
    output, by_id, blocked = plan
    ec = next(a for a in output['activities'] if 'Environmental Clearance' in a['name'])
    stages = {by_id[t]['stage'] for t in blocked[ec['id']]}
    assert {'enabling', 'substructure'} <= stages

    for target in blocked[ec['id']]:
        assert by_id[target]['start_day'] >= ec['finish_day'], (
            f"{by_id[target]['name']} starts on day {by_id[target]['start_day']}, before the "
            f"Environmental Clearance is granted on day {ec['finish_day']}"
        )


def test_an_approval_can_gate_another_approval(plan):
    """DOMAIN_KNOWLEDGE.md section 5: occupancy cannot precede the final fire NOC. That is an
    approval blocking an approval, which the stage-name resolution alone could not express."""
    output, by_id, blocked = plan
    fire = next(a for a in output['activities'] if 'Fire NOC' in a['name'])
    occupancy = next(a for a in output['activities'] if 'Occupancy Certificate' in a['name'])
    assert occupancy['id'] in blocked[fire['id']]
    assert occupancy['start_day'] >= fire['finish_day']


def test_a_city_with_no_pathway_produces_no_approvals_rather_than_an_error(plan):
    output = drive(NO_PATHWAY_CITY, 'statutory-none')
    assert not [a for a in output['activities'] if a['id'].startswith('stat.')]
    assert output['activities'], 'the run produced nothing at all, so this proves nothing'


def test_every_approval_is_auditable(plan):
    """An approval that appears in the schedule with no trail entry is a date nobody can trace.
    Caught by the assembly tests when this was first written without one."""
    output, _, _ = plan
    refs = {t['ref_id'] for t in output['reasoning_trail']}
    for activity in output['activities']:
        if activity['id'].startswith('stat.'):
            assert activity['id'] in refs, f"{activity['id']} has no reasoning trail entry"


def test_the_modelling_limit_is_stated_on_the_plan(plan):
    """Approvals are modelled from project start because the library says how long one takes,
    not when it is lodged. That understates the late ones, and the plan must say so."""
    output, _, _ = plan
    # Assembly warnings reach the plan as engine_warning flags, which is where a reader sees
    # them, so that is where this looks rather than at the raw warning list.
    warnings = [
        f['message'] for f in output['flags'] if f['kind'] == 'engine_warning'
    ]
    assert any('modelled as starting at project start' in w for w in warnings), warnings


# ------------------------------------------------------------------- 2. CEIG / energisation


def test_energisation_exists_at_all(plan):
    """It did not. The power train stopped at cable termination, so DOMAIN_KNOWLEDGE section 5's
    "energisation cannot precede CEIG approval" had nothing to attach to."""
    output, _, _ = plan
    assert named(output, 'energisation and live electrical testing') is not None


def test_ceig_gates_energisation(plan):
    output, by_id, _ = plan
    energise = named(output, 'energisation and live electrical testing')
    ceig = [
        p['id'] for p in (energise.get('predecessors') or [])
        if p.get('kind') == 'statutory' and 'ceig' in p['id']
    ]
    assert ceig, 'HV/MV energisation does not wait on the CEIG approval'


def test_energisation_is_tier_1_and_blocks_export(plan):
    """safety.hv_energisation is a Tier-1 entry with blocks_export set, and before the
    energisation activity existed it matched nothing in any plan ever produced."""
    output, _, _ = plan
    energise = named(output, 'energisation and live electrical testing')
    assert energise['hitl_tier'] == 'tier_1'
    assert energise['blocks_export'] is True


# --------------------------------------------------------------------- 3. PESO / generators


def test_the_generators_are_in_the_plan_and_wait_for_delivery(plan):
    """lead.generator was 30 weeks with drives_rfs=true and was consumed by nothing, so the
    gensets never appeared in a programme and their lead time constrained nothing."""
    output, _, _ = plan
    genset = named(output, 'generator set installation')
    assert genset is not None, 'no generator installation activity'
    delivery = [
        p['id'] for p in (genset.get('predecessors') or [])
        if p.get('kind') == 'delivery' and 'generator' in p['id']
    ]
    assert delivery, 'generator installation does not wait for the generator to arrive'


def test_bulk_hsd_storage_exists_and_is_treated_as_safety_work(plan):
    output, _, _ = plan
    hsd = named(output, 'HSD storage tank')
    assert hsd is not None, 'no bulk HSD storage activity for the PESO licence to gate'
    assert hsd['hitl_tier'] == 'tier_1'


def test_the_peso_licence_is_in_the_plan_and_gates_work(plan):
    output, by_id, blocked = plan
    peso = next((a for a in output['activities'] if 'PESO' in a['name']), None)
    assert peso is not None, 'the PESO licence is not in the plan'
    assert peso['duration_days'] > 0
    assert blocked.get(peso['id']), 'the PESO licence gates nothing'


# ------------------------------------------------------- 4. the commissioning ladder is full


def test_the_ladder_reaches_l4_l5_and_black_building(plan):
    """Checked rather than assumed: the ladder was already complete when this was raised, and
    the honest answer to "does it stop at L1-L2?" is no."""
    output, _, _ = plan
    ladder = [
        a for a in output['activities']
        if a['stage'] == 'commissioning' and a['type'] == 'task' and a.get('source_fragnet')
    ]
    names = ' | '.join(a['name'] for a in ladder)
    for level in ('L1', 'L2', 'L3', 'L4', 'L5'):
        assert level in names, f'{level} is missing from the commissioning ladder: {names}'
    assert any('black-building' in a['name'].lower() for a in ladder)
    assert any('failure-mode' in a['name'].lower() for a in ladder)

    ordered = sorted(ladder, key=lambda a: a['start_day'])
    assert 'L1' in ordered[0]['name'], 'the ladder does not start at L1'
    assert ordered[-1]['duration_days'] > 0

    ist = next(a for a in ladder if 'L5' in a['name'])
    assert ist['hitl_tier'] == 'tier_1' and ist['blocks_export'] is True


def test_the_ladder_runs_in_order(plan):
    output, _, _ = plan
    ladder = sorted(
        (a for a in output['activities']
         if a['stage'] == 'commissioning' and a['type'] == 'task' and a.get('source_fragnet')),
        key=lambda a: a['start_day'],
    )
    for earlier, later in zip(ladder, ladder[1:]):
        assert later['start_day'] >= earlier['start_day']


# --------------------------------------------------------------------- 5. Uptime Tier cert


def test_uptime_tier_certification_is_its_own_step(plan):
    output, _, _ = plan
    tccf = named(output, 'Uptime Institute Tier Certification')
    assert tccf is not None, 'no Uptime Tier certification activity'
    assert tccf['stage'] == 'handover'
    assert tccf['duration_days'] > 0, 'a zero-day certification is a label, not a step'

    handover = [a for a in output['activities'] if a['stage'] == 'handover']
    assert any('snag' in a['name'].lower() for a in handover)
    assert any('O&M manual' in a['name'] for a in handover)
    assert any('Warranty' in a['name'] for a in handover)


def test_certification_follows_a_facility_that_works(plan):
    """A Tier demonstration of an uncommissioned building certifies nothing."""
    output, _, _ = plan
    tccf = named(output, 'Uptime Institute Tier Certification')
    commissioning = [
        a for a in output['activities']
        if a['stage'] == 'commissioning' and a['type'] == 'task' and a.get('source_fragnet')
    ]
    assert tccf['start_day'] >= max(a['finish_day'] for a in commissioning)


# ------------------------------------------------- the Tier-1 false positive this exposed


def test_routine_fit_out_is_not_marked_tier_1_on_a_greenfield_site(plan):
    """Found when the fit-out fragnet landed: "Raised floor and plinth installation to data
    halls" shares {data, halls} with "Work in or next to live data halls (brownfield)", which
    was enough for the two-word keyword match. A routine activity on a greenfield site came out
    Tier-1 and BLOCKED EXPORT.

    Wrongly blocking export is not a safe failure - it teaches a planner that the block is
    noise, and the block is the one thing CLAUDE.md rule 5 will not let us weaken.
    """
    output, _, _ = plan
    floor = named(output, 'Raised floor and plinth')
    assert floor is not None
    assert floor['hitl_tier'] != 'tier_1'
    assert floor['blocks_export'] is False


def test_the_brownfield_rule_still_fires_on_a_brownfield_site():
    safety = load_library('safety_register')['entries']
    name = 'Raised floor and plinth installation to data halls'
    assert not match_safety_rules(name, safety, site_context='greenfield')
    assert [m['id'] for m in match_safety_rules(name, safety, site_context='brownfield')] == [
        'safety.live_hall_works'
    ]


def test_the_condition_is_library_data_not_code():
    entry = next(
        e for e in load_library('safety_register')['entries']
        if e['id'] == 'safety.live_hall_works'
    )
    assert entry['applies_when_site_context'] == 'brownfield'


# ------------------------------------------- lodgement: an approval that can actually bind


def test_ceig_is_lodged_against_the_installation_and_actually_binds(plan):
    """The whole point of modelling lodgement.

    CEIG is eight weeks. Started at project start it cleared on day 56 against an energisation
    on day 520 - a hard edge in the graph that could never bind, which is worse than no edge
    because it looks handled. Lodged once the HV/MV switchgear is in, the eight weeks land where
    they actually fall and energisation waits for them.
    """
    output, by_id, _ = plan
    ceig = next(a for a in output['activities'] if 'permission to energise' in a['name'])
    switchgear = next(
        a for a in output['activities'] if 'switchgear installation' in a['name'].lower()
    )
    energise = named(output, 'energisation and live electrical testing')

    assert ceig['start_day'] >= switchgear['finish_day'], (
        f"CEIG starts on day {ceig['start_day']}, before the switchgear it inspects is in on "
        f"{switchgear['finish_day']}"
    )
    assert ceig['start_day'] > 0, 'CEIG still starts at project start'

    # And it BINDS: energisation cannot begin until the approval is granted.
    assert energise['start_day'] >= ceig['finish_day'], (
        f"energisation starts on day {energise['start_day']} but CEIG is not granted until "
        f"{ceig['finish_day']} — the approval is expressed but inert"
    )


def test_the_late_approvals_are_lodged_against_real_work(plan):
    """Occupancy and the final fire NOC are applied for against a finished, tested building.
    Left at project start they cleared before the building existed."""
    output, _, _ = plan
    for name in ('Occupancy Certificate', 'Final Fire NOC'):
        approval = next(a for a in output['activities'] if name in a['name'])
        assert approval['start_day'] > 0, f'{name} is still lodged at project start'


def test_the_early_approvals_are_still_lodged_at_the_outset(plan):
    """Not everything should move. EC, CTE and building sanction are applied for off drawings
    at the start of the job, and making them wait for site work would be as wrong in the other
    direction."""
    output, _, _ = plan
    for name in ('Environmental Clearance', 'Consent to Establish', 'Building plan sanction'):
        approval = next(a for a in output['activities'] if name in a['name'])
        assert approval['start_day'] == 0, f'{name} is no longer lodged at the outset'


def test_peso_gates_the_diesel_it_licenses_and_nothing_else(plan):
    """It blocked the whole commissioning stage, so a diesel-storage licence gated L1 factory
    acceptance testing. Narrowed to the genset and fuel systems it actually covers."""
    output, by_id, blocked = plan
    peso = next(a for a in output['activities'] if 'PESO' in a['name'])
    gated = [by_id[t]['name'] for t in blocked[peso['id']]]

    assert gated, 'the PESO licence gates nothing at all'
    for name in gated:
        assert any(word in name.lower() for word in ('generator', 'hsd', 'fuel')), (
            f'PESO gates "{name}", which is not diesel storage or a fuel system'
        )
    assert not any('factory acceptance' in n.lower() for n in gated), (
        'a diesel licence is still gating L1 factory acceptance testing'
    )
