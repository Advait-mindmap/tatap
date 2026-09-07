"""How work is LET is a fact about the work, not about the stage it sits in.

Reported from an audit: a brief stating BMS self-performed and fire suppression subcontracted
produced BMS activities assigned to a subcontract package. Civil and electrical were correct,
which made it look BMS-specific.

It was not. Delivery mode was resolved once per STAGE, from a table mapping each stage to a
single delivery discipline - `fire_bms -> fire`. Every activity in that stage inherited the fire
scope's mode, BMS included. Seven of the twelve fragnets bundle work the stage cannot represent
that way, and fit-out was worse than fire_bms: nine electrical leaves were being let as civil.

The resource export was innocent. It read the delivery mode the engine recorded, faithfully; the
recorded value was already wrong. That distinction matters for where the guard belongs - at the
point of resolution, not at the point of use.
"""

from __future__ import annotations

import collections

import pytest

from backend.app.engine.assemble import BRIEF_DISCIPLINE, STAGE_DISCIPLINE, _discipline_of, _expand_steps
from backend.app.intake import extract_brief
from backend.app.libraries import load_library
from backend.app.llm_stub import StubAdapter
from backend.app.simulator import DecisionAnswer, Simulator

#: States a different mode for BMS than for fire, in one sentence each, so the two halves of the
#: fire_bms fragnet must resolve differently or the bug is back.
BRIEF_TEXT = """We are bidding a 30 MW Tier IV data centre in Chennai, greenfield. Topology is 2N.
Delivery: we self-perform civil and structure. Electrical and mechanical are turnkey specialist
packages. Fire suppression is subcontracted. BMS is self-performed."""


@pytest.fixture(scope='module')
def plan():
    brief = extract_brief(BRIEF_TEXT, adapter=StubAdapter()).brief.model_dump()
    simulator = Simulator(brief, run_id='per-leaf', adapter=StubAdapter())
    for _ in range(40):
        list(simulator.run())
        if not simulator.is_halted:
            break
        for fork in sorted(simulator.state.pending_decisions):
            simulator.answer(DecisionAnswer(decision_point_id=fork, answer='Proceed'))
    return brief, simulator.output().model_dump()


# ------------------------------------------------------------------ the reported case

def test_the_brief_states_different_modes_for_the_two_halves_of_one_fragnet(plan):
    """Guards the guard: if extraction stopped reading one of them, everything below is vacuous."""
    brief, _ = plan
    modes = brief['delivery_mode_by_discipline']
    assert modes.get('bms') == 'self-perform', modes
    assert modes.get('fire') == 'subcontract', modes


def test_bms_work_is_self_performed_when_the_brief_says_so(plan):
    _, output = plan
    controls = [a for a in output['activities'] if a.get('discipline') == 'controls']
    assert controls, 'no BMS work in the plan'
    modes = {a['delivery_mode'] for a in controls}
    assert modes == {'self-perform'}, f'BMS work resolved as {sorted(modes)}'


def test_the_fire_half_of_the_same_fragnet_stays_subcontracted(plan):
    """The fix must SPLIT the bundle, not flip it. Both halves come from one fragnet."""
    _, output = plan
    fire = [a for a in output['activities']
            if a.get('discipline') == 'fire' and a['stage'] == 'fire_bms']
    assert fire
    assert {a['delivery_mode'] for a in fire} <= {'subcontract', 'unknown'}

    bundled = {a['source_fragnet'] for a in output['activities']
               if a['stage'] == 'fire_bms' and a.get('source_fragnet')}
    assert 'frag.fire_bms.detection_suppression' in bundled, (
        'the two halves are no longer in one fragnet, so this no longer tests bundling'
    )


# ------------------------------------------------------------------ the general rule

def test_no_activity_contradicts_the_mode_its_brief_states(plan):
    """The systematic form. Every leaf whose discipline the brief speaks about must match it."""
    brief, output = plan
    stated = brief['delivery_mode_by_discipline']
    wrong = []
    for activity in output['activities']:
        discipline = activity.get('discipline')
        key = BRIEF_DISCIPLINE.get(discipline or '')
        if key and key in stated and activity['delivery_mode'] != 'unknown':
            if activity['delivery_mode'] != stated[key]:
                wrong.append((activity['id'], discipline, activity['delivery_mode'], stated[key]))
    assert not wrong, f'{len(wrong)} activities contradict the brief, e.g. {wrong[:3]}'


def test_every_bundled_fragnet_resolves_its_leaves_separately(plan):
    """Seven of twelve fragnets bundle disciplines. Each must resolve per leaf, not per stage."""
    brief, output = plan
    stated = brief['delivery_mode_by_discipline']

    by_fragnet = collections.defaultdict(set)
    for activity in output['activities']:
        if activity.get('source_fragnet') and activity.get('discipline'):
            by_fragnet[activity['source_fragnet']].add(
                (activity['discipline'], activity['delivery_mode'])
            )

    split = 0
    for fragnet, pairs in by_fragnet.items():
        modes = {mode for _, mode in pairs if mode != 'unknown'}
        disciplines = {d for d, _ in pairs}
        keys = {BRIEF_DISCIPLINE.get(d) for d in disciplines} & set(stated)
        if len(keys) > 1:
            # The brief speaks about more than one discipline in this fragnet, so the fragnet
            # must carry more than one mode - unless the brief happens to state the same for all.
            expected = {stated[k] for k in keys}
            assert modes >= expected or len(expected) == 1, (
                f'{fragnet} spans {sorted(keys)} but resolved to {sorted(modes)}'
            )
            if len(expected) > 1:
                split += 1
    assert split >= 1, 'no fragnet in this plan spans two stated modes, so this proves nothing'


def test_a_discipline_the_brief_is_silent_about_falls_back_to_its_stage(plan):
    """The fallback is the old behaviour and is right: a mode nobody stated is not a mode."""
    brief, output = plan
    stated = brief['delivery_mode_by_discipline']
    unmapped = [
        a for a in output['activities']
        if a.get('discipline') and BRIEF_DISCIPLINE.get(a['discipline']) not in stated
    ]
    assert unmapped, 'every discipline is stated, so the fallback is untested here'
    for activity in unmapped:
        stage_key = STAGE_DISCIPLINE.get(activity['stage'], '')
        expected = stated.get(stage_key, 'unknown')
        assert activity['delivery_mode'] == expected, (
            f'{activity["id"]} ({activity["discipline"]}) took {activity["delivery_mode"]}, '
            f'not its stage default {expected}'
        )


# ------------------------------------------------------------------ the library audit

def test_the_library_really_does_bundle_disciplines():
    """If fragnets stopped bundling, the bug could not recur - and these tests would be theatre.

    This asserts the condition that makes the fix necessary, so the suite tells the truth about
    why it exists.
    """
    bundled = []
    for entry in load_library('fragnets')['entries']:
        specs, _ = _expand_steps(entry.get('activities'))
        keys = {BRIEF_DISCIPLINE.get(_discipline_of(spec, entry)) for spec in specs}
        keys.discard(None)
        if len(keys) > 1:
            bundled.append((entry['id'], sorted(keys)))
    assert len(bundled) >= 5, f'only {len(bundled)} fragnets bundle disciplines: {bundled}'


# ------------------------------------------------------------------ through to the export

def test_the_resource_assignment_follows_the_resolved_mode(plan):
    """Where this surfaced: BMS work assigned to a subcontract package it is not in."""
    xer_reader = pytest.importorskip('xer_reader', reason='xer-reader is needed to read back')
    import pathlib
    import tempfile
    from datetime import datetime

    from backend.app.p6.xer import export_bytes

    _, output = plan
    path = pathlib.Path(tempfile.mkdtemp()) / 'leaf.xer'
    path.write_bytes(export_bytes(output, start_date=datetime(2027, 1, 4)))
    tables = xer_reader.XerReader(str(path)).to_dict()

    resources = {r['rsrc_id']: r for r in tables['RSRC'].entries()}
    names = [r['rsrc_name'] for r in resources.values()]

    # BMS is self-performed, so it is our crew and not a package.
    controls = [n for n in names if n.startswith('Controls')]
    assert controls, names
    assert all('package' not in n for n in controls), (
        f'BMS is self-performed but appears as a let package: {controls}'
    )
    # Fire is subcontracted, so it is a package and not our crew.
    fire = [n for n in names if n.startswith('Fire')]
    assert any('subcontract package' in n for n in fire), fire
