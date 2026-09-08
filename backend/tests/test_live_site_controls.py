"""A brownfield plan with no live-site safety control must not export as if it were complete.

Found on a real production export: a run answered "Brownfield - inside a live hall" produced 486
activities containing NOT ONE concurrent-operations control - no permit to work, no isolation, no
change-freeze hold. The site-context fork fired correctly and the answer was written back
correctly; what failed was downstream.

`match_safety_rules` pairs an activity NAME against a rule's `activity_pattern` and needs two
shared keywords. The patterns are written as descriptions - "Work in or next to live data halls
(brownfield)" - so across all 199 library activity names the best overlap any name achieves is
one. Two of the five tier-1 rules therefore match nothing at all, ever, under any site context.

That is a library and mechanism problem, and the honest fix is not to guess which activities carry
a live-hall permit: on a brownfield site that is a property of what is ADJACENT and ENERGISED, not
of what an activity is called. Until that exists, the plan must at least refuse to look finished.
The export gate is exactly the instrument for it - absence of a tier-1 control on a live site is
the condition the gate exists for - and it releases the same way any tier-1 does, on a named
signature, so a person can still ship it deliberately and be recorded as having done so.
"""

from __future__ import annotations

import pytest

from backend.app.libraries import load_library
from backend.app.simulator import DecisionAnswer, Simulator
from backend.tests.test_silent_fork_defaults import SilentAdapter

BRIEF = {
    'project_name': 'Live site', 'city': 'Navi Mumbai', 'tier': 'III',
    'it_load_mw': 20.0, 'redundancy_topology': 'N+1',
    'delivery_mode_by_discipline': {'civil': 'self-perform'},
}


def run(site_context):
    simulator = Simulator(dict(BRIEF, site_context=site_context), adapter=SilentAdapter())
    for _ in range(40):
        list(simulator.run())
        if not simulator.is_halted:
            break
        for fork in sorted(simulator.state.pending_decisions):
            payload = simulator.state.pending_decisions[fork]
            simulator.answer(DecisionAnswer(
                decision_point_id=fork, answer=(payload.get('options') or ['Confirmed'])[0],
            ))
    return simulator.output()


def live_site_rules():
    return [
        r for r in load_library('safety_register')['entries']
        if (r.get('applies_when_site_context') or '').strip().lower() == 'brownfield'
        and r.get('hitl_tier') == 'tier_1'
    ]


def test_the_library_still_has_a_live_site_rule_to_be_missing():
    """If this fails the premise is gone and the tests below prove nothing."""
    assert live_site_rules(), 'no tier-1 rule is gated on brownfield any more'


def test_a_brownfield_plan_missing_its_live_site_control_blocks_export():
    quality = run('brownfield').quality
    assert quality['export_blocked'], 'a brownfield plan with no live-site control exported freely'
    assert 'live' in quality['export_block_reason'].lower(), (
        f'the block does not say what is missing: {quality["export_block_reason"]!r}'
    )


def test_the_reason_names_the_control_that_is_absent(): 
    reason = run('brownfield').quality['export_block_reason']
    for rule in live_site_rules():
        assert rule['id'] in reason, f'{rule["id"]} is absent from the plan and unnamed in {reason!r}'


def test_a_greenfield_plan_is_not_blocked_for_a_control_it_does_not_need():
    """The rule does not apply off a live site, so its absence is not a fault there."""
    reason = run('greenfield').quality['export_block_reason']
    for rule in live_site_rules():
        assert rule['id'] not in reason, (
            f'a greenfield plan was blocked for {rule["id"]}, which only applies to brownfield'
        )


def test_the_block_lifts_if_the_control_ever_lands():
    """Guards against this becoming permanent. When the real mechanism attaches a live-hall
    control, this block must stop firing on its own rather than needing to be remembered."""
    from backend.app.engine.assemble import _missing_live_site_controls

    matched = {r['id'] for r in live_site_rules()}
    assert _missing_live_site_controls('brownfield', matched, load_library('safety_register')['entries']) == []
