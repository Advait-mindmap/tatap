"""Tier 3: a stage splits into the sub-packages an EPC schedule is actually let in.

The package level of the WBS used to divide nothing. The engine numbered packages by SELECTION -
which fragnet an activity came from - and a stage carries one fragnet, so every activity in
substructure sat under `05.01` and the middle segment of a three-part code was decoration. That
is why Tier 1's nesting bought so little: it had a level with nothing to put in it.

The package is now the activity's DISCIPLINE, recorded per leaf in the library. Substructure
separates into earthworks, steelfixing, concrete and cube testing; enabling separates civil works
from temporary power from the BOCW and CLRA registrations. Those are different subcontracts and
different people, which is the test of whether a breakdown is real.

Measured on the Navi Mumbai brief: 42 distinct stage.package paths, and the exported WBS goes
from 24 nodes to 130.
"""

from __future__ import annotations

import collections

import pytest

from backend.app.engine.assemble import DISCIPLINE_ORDER
from backend.app.libraries import load_library
from backend.app.llm_stub import StubAdapter
from backend.app.p6.xer import build_wbs
from backend.app.simulator import DecisionAnswer, Simulator

BRIEF = {
    'project_name': 'Discipline packages', 'city': 'Navi Mumbai', 'tier': 'III',
    'it_load_mw': 20.0, 'redundancy_topology': 'N+1', 'site_context': 'greenfield',
}


@pytest.fixture(scope='module')
def plan():
    simulator = Simulator(BRIEF, run_id='disciplines', adapter=StubAdapter())
    for _ in range(40):
        list(simulator.run())
        if not simulator.is_halted:
            break
        for fork in sorted(simulator.state.pending_decisions):
            simulator.answer(DecisionAnswer(decision_point_id=fork, answer='Proceed'))
    assert not simulator.is_halted, 'the walk never completed'
    return simulator.output().model_dump()


def leaves():
    """Every library leaf: the step where an activity is decomposed, the activity where not."""
    for entry in load_library('fragnets')['entries']:
        for spec in entry['activities']:
            steps = spec.get('steps') or []
            if steps:
                for step in steps:
                    yield entry, spec, step
            else:
                yield entry, spec, spec


# ------------------------------------------------------------------ the library

def test_every_leaf_declares_a_discipline():
    """A leaf without one falls back to the fragnet's department, which puts a whole stage in one
    package again - the exact condition this tier exists to remove, and silently."""
    for entry, spec, leaf in leaves():
        assert leaf.get('discipline'), f'{entry["id"]}:{spec["id"]} has a leaf with no discipline'


def test_every_discipline_is_one_the_engine_knows_how_to_order():
    """An unknown name sorts to the end rather than to position zero, but it also means the P6
    export has no readable label for it - so the vocabulary is closed on purpose."""
    for entry, spec, leaf in leaves():
        assert leaf['discipline'] in DISCIPLINE_ORDER, (
            f'{entry["id"]}:{spec["id"]} uses discipline {leaf["discipline"]!r}, '
            f'which is not in DISCIPLINE_ORDER'
        )


def test_the_split_is_declared_unverified_like_the_rest_of_the_library():
    for entry in load_library('fragnets')['entries']:
        provenance = entry.get('provenance') or {}
        assert provenance.get('verification_status') == 'unverified'
        assert 'DISCIPLINE PACKAGES' in (provenance.get('note') or ''), entry['id']


def test_most_stages_really_do_split():
    """If nearly every stage resolved to one package this tier would be structure without
    content - which is precisely what it replaced."""
    per_stage = collections.defaultdict(set)
    for entry, _, leaf in leaves():
        per_stage[entry['stage']].add(leaf['discipline'])
    split = [stage for stage, kinds in per_stage.items() if len(kinds) > 1]
    assert len(split) >= 9, f'only {len(split)} of {len(per_stage)} stages split into packages'


# ------------------------------------------------------------------ the plan

def test_the_wbs_package_segment_is_the_discipline(plan):
    """Two activities share a package path if and only if they share a discipline."""
    by_path = collections.defaultdict(set)
    for activity in plan['activities']:
        parts = str(activity.get('wbs_id') or '').split('.')
        if len(parts) == 3 and parts[0] != '00' and activity.get('discipline'):
            by_path['.'.join(parts[:2])].add(activity['discipline'])
    assert by_path
    for path, disciplines in by_path.items():
        assert len(disciplines) == 1, f'{path} mixes {sorted(disciplines)}'


def test_no_two_pieces_of_WORK_share_a_wbs_path(plan):
    """Activity numbers run within a package now. Numbering them within the fragnet instead
    would give two disciplines the same path and collapse them in any tool that groups on it.

    A hold point deliberately shares its activity's path - it is that activity's inspection, not
    a separate line of work - so the invariant is about tasks.
    """
    paths = [
        a['wbs_id'] for a in plan['activities']
        if str(a['wbs_id']).split('.')[0] != '00' and a['type'] == 'task'
    ]
    duplicates = [path for path, count in collections.Counter(paths).items() if count > 1]
    assert not duplicates, f'{len(duplicates)} WBS paths are used twice, e.g. {duplicates[:3]}'


def test_a_hold_is_filed_with_the_work_it_holds(plan):
    """Found by the duplicate-path check above, and worth its own test.

    "Reinforcement inspection" hangs off the end of the raft reinforcement package. Once that
    deliverable was decomposed, its LAST step turned out to be the cast-in earth pits - which
    Tier 3 classifies as electrical - so the inspection was filed under electrical, where no
    steelfixing engineer would look for it. A hold takes its deliverable's package, not its
    last step's.
    """
    holds = [a for a in plan['activities'] if a['type'] == 'hold_point']
    assert holds
    rebar_hold = next(a for a in holds if 'Reinforcement inspection' in a['name'])
    assert rebar_hold['discipline'] == 'structural', (
        f'the reinforcement inspection is filed under {rebar_hold["discipline"]}'
    )

    by_id = {a['id']: a for a in plan['activities']}
    for hold in holds:
        owners = [by_id[p['id']] for p in hold['predecessors'] if p['id'] in by_id]
        work = [o for o in owners if o['type'] == 'task']
        if not work:
            continue
        # It sits in a package, and that package holds real work of the same discipline.
        assert hold['discipline'], f'{hold["id"]} has no package'
        assert any(a['discipline'] == hold['discipline'] and a['type'] == 'task'
                   for a in plan['activities'] if a['stage'] == hold['stage']), (
            f'{hold["name"]} is filed in a package with no work of that discipline'
        )


def test_a_deliverable_that_spans_disciplines_is_split_across_them(plan):
    """The reason the discipline sits on the STEP rather than the activity.

    Raft reinforcement fixing is steelfixing, but the cast-in earth pits inside it are
    electrical scope. A package split that could not see inside a deliverable would file the
    whole thing under one trade and hide the interface.
    """
    rebar = [a for a in plan['activities']
             if a['name'].startswith('Raft reinforcement fixing')]
    assert rebar, 'the raft reinforcement activity is not in the plan'
    disciplines = {a['discipline'] for a in rebar}
    assert disciplines == {'structural', 'electrical'}, (
        f'raft reinforcement resolved to {sorted(disciplines)}'
    )


def test_the_exported_wbs_gains_the_package_level(plan):
    nodes, assignment = build_wbs(plan, 'Navi Mumbai DC', 1, 1)
    assert len(nodes) > 100, f'the WBS is {len(nodes)} nodes; the package level did not engage'

    by_id = {n['wbs_id']: n for n in nodes}
    root = next(n for n in nodes if n['proj_node_flag'] == 'Y')
    children = collections.defaultdict(list)
    for node in nodes:
        if node['parent_wbs_id'] != '':
            children[node['parent_wbs_id']].append(node)

    # At least one ordinary stage carries several named packages beneath it.
    named = [
        [c['wbs_name'] for c in children[stage['wbs_id']]]
        for stage in children[root['wbs_id']]
        if len(children[stage['wbs_id']]) > 1 and stage['wbs_short_name'] != '00'
    ]
    assert named, 'no stage has more than one package below it'
    assert any('Civil works' in group for group in named), (
        f'no stage shows a named discipline package: {named[:3]}'
    )
    assert all(a in by_id for a in assignment.values())


def test_the_split_moves_no_dates(plan):
    """A package boundary is a reporting decision. Redrawing one must never move a date, or the
    schedule would change according to how the work was filed."""
    assert plan['rfs_day'] == 840, (
        f'RFS is {plan["rfs_day"]}, not the 840 the plan had before the packages were drawn'
    )


def test_the_flow_shows_one_package_node_per_discipline(plan):
    """A node per fragnet was a node per stage, sitting beside the stage header saying the same
    thing twice."""
    packages = [n for n in plan['flow']['nodes'] if n['kind'] == 'work_package']
    assert packages
    for node in packages:
        assert node.get('discipline'), f'{node["id"]} is a package with no discipline'
        assert node['label'] != node['discipline'], 'the package is labelled with its raw code'

    # And every activity hangs off the package it belongs to.
    ids = {n['id'] for n in packages}
    for node in plan['flow']['nodes']:
        if node['kind'] == 'activity' and node.get('discipline'):
            assert node['parent'] in ids, f'{node["id"]} points at a package that does not exist'
