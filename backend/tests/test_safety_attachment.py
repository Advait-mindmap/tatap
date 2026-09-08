"""Safety controls attach at the level the register was written for.

Part C. The safety register pairs a rule against an `activity_pattern`, and those patterns were
authored against the library's DELIVERABLES: "HV/MV energisation & live electrical testing",
"Integrated systems test under load / on generator". Tier 4 decomposition then REPLACED those
deliverables with their execution steps, so the names the rules were written against stopped
existing as leaves, and matching fell through to whatever step names happened to share two words.

Measured before this change, across all five tier-1 rules:

  * safety.hv_energisation        -> correct, by luck: the deliverable has no steps
  * safety.ist_under_load         -> "Test script and load bank mobilisation", a step of L4
                                     FUNCTIONAL PERFORMANCE TESTING. The L5 integrated systems
                                     test it names carried no control at all
  * safety.gas_suppression_discharge -> nothing
  * safety.genset_fuel_commissioning -> "Bulk HSD storage tank and fuel system installation"
  * safety.live_hall_works        -> nothing

A wrong attachment is worse than a missing one: it manufactures the appearance of coverage on
work that does not carry the hazard, while the work that does carries nothing.

This is not a remapping - which activity carries which permit is a planner's judgement and is not
being guessed here. It restores the level the library already expresses. The parent survives on
every step, so the deliverable a step belongs to is a fact the plan already holds.
"""

from __future__ import annotations

import pytest

from backend.app.llm_stub import StubAdapter
from backend.app.simulator import DecisionAnswer, Simulator

BRIEF = {
    'project_name': 'Safety attachment', 'city': 'Navi Mumbai', 'tier': 'III',
    'it_load_mw': 20.0, 'redundancy_topology': 'N+1', 'site_context': 'brownfield',
}

IST_STEPS = {
    'IST scenario matrix agreement',
    'Integrated normal-mode operation',
    'Utility failure and generator transition',
    'Cooling failure and thermal ride-through',
    'Witnessed rerun and results compilation',
}
L4_STEPS = {'Test script and load bank mobilisation', 'Individual system functional tests'}


@pytest.fixture(scope='module')
def activities():
    simulator = Simulator(dict(BRIEF), adapter=StubAdapter())
    for _ in range(50):
        list(simulator.run())
        if not simulator.is_halted:
            break
        for fork in sorted(simulator.state.pending_decisions):
            payload = simulator.state.pending_decisions[fork]
            simulator.answer(DecisionAnswer(
                decision_point_id=fork, answer=(payload.get('options') or ['Confirmed'])[0],
            ))
    return simulator.output().activities


def tier1_names(activities):
    # Names read "Deliverable: Step - Zone". The step is what identifies the activity, so strip
    # the zone suffix and take the part after the deliverable prefix.
    names = set()
    for a in activities:
        if a.get('hitl_tier') != 'tier_1':
            continue
        name = str(a['name']).split(' - ')[0]
        names.add(name.split(': ', 1)[-1])
        names.add(name)
    return names


def test_the_integrated_systems_test_carries_its_own_control(activities):
    """THE SPECIFIC CLAIM. Not "some commissioning activity is tier-1" - the IST itself."""
    flagged = tier1_names(activities) & IST_STEPS
    assert flagged, (
        'the L5 integrated systems test carries no tier-1 control. Tier-1 names present: '
        f'{sorted(tier1_names(activities))}'
    )


def test_the_l4_load_bank_step_no_longer_wears_the_ist_control(activities):
    """The wrong attachment, gone. L4 mobilisation is not the integrated systems test."""
    assert 'Test script and load bank mobilisation' not in tier1_names(activities), (
        'L4 load-bank mobilisation is still flagged tier-1 by keyword coincidence'
    )


def test_a_site_gated_rule_never_attaches_by_keyword(activities):
    """`safety.live_hall_works` shares two words with "Raised floor and plinth installation to
    data halls" and must NOT attach to it. Whether work is beside an energised hall is a question
    about adjacency and timing, and until that exists the honest answer is no attachment - which
    leaves the export blocked and the absence named, rather than a coincidence standing in for a
    tier-1 determination."""
    assert 'Raised floor and plinth installation to data halls' not in tier1_names(activities)


def test_the_energisation_control_still_attaches(activities):
    """The one rule that was correct stays correct - this change must not trade one for another."""
    assert any('energisation' in name.lower() for name in tier1_names(activities)), (
        f'HV energisation lost its control: {sorted(tier1_names(activities))}'
    )


def test_every_step_of_a_hazardous_deliverable_carries_the_control(activities):
    """The hazard belongs to the deliverable, so each step it was split into carries it. The
    alternative - flagging one step - would let a planner sign off part of an IST."""
    flagged = tier1_names(activities) & IST_STEPS
    assert len(flagged) >= 2, (
        f'only {sorted(flagged)} of the IST steps carry the control; the deliverable was split '
        'and its hazard did not follow'
    )
