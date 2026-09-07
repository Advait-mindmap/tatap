"""The reasoning trail is read by a planner, not by whoever wrote the identifiers.

Reported from a real run: sentences arrived reading "formally confirmed via
dp.dyn.low_confidence.frag.superstructure.steel ('Yes, it applies')" and "consistent with the
resolved dp.delivery_mode". Every fact in that is true and most of it is unreadable - they are
variable names, and a trail that demands them defeats the one thing it exists for.

WHAT MUST NOT CHANGE, and is asserted here as firmly as what must: WBS codes, stage, department
and duration labels, and activity names. Those are real industry notation. A planner reads
`05.03.001` fluently, and a fix that scrubbed them would be a worse regression than the bug.
"""

from __future__ import annotations

import re

import pytest

from backend.app.libraries import load_library
from backend.app.llm_stub import StubAdapter
from backend.app.readable import build_labels, humanise
from backend.app.schemas import TrailEntry
from backend.app.simulator import DecisionAnswer, Simulator
from backend.app.simulator.output import _readable_trail

#: Anything shaped like one of our internal identifiers.
IDENTIFIER = re.compile(r'\b(?:dp|dyn|frag|gate|lead|path|safety|stat|zone)(?:\.[A-Za-z0-9_-]+)+')

REPORTED = (
    "Instanced from frag.superstructure.steel (Superstructure - structural steel frame), "
    "selected because: the frame is in scope, formally confirmed via "
    "dp.dyn.low_confidence.frag.superstructure.steel ('Yes, it applies') and consistent with "
    "the resolved dp.delivery_mode."
)


@pytest.fixture(scope='module')
def labels():
    return build_labels(
        load_library('fragnets')['entries'],
        load_library('decision_points')['entries'],
        load_library('equipment_lead_times')['entries'],
        load_library('safety_register')['entries'],
    )


@pytest.fixture(scope='module')
def plan():
    simulator = Simulator(
        {'project_name': 'x', 'city': 'Navi Mumbai', 'tier': 'III', 'it_load_mw': 20.0,
         'redundancy_topology': 'N+1', 'site_context': 'greenfield'},
        run_id='readable', adapter=StubAdapter(),
    )
    for _ in range(40):
        list(simulator.run())
        if not simulator.is_halted:
            break
        for fork in sorted(simulator.state.pending_decisions):
            simulator.answer(DecisionAnswer(decision_point_id=fork, answer='Proceed'))
    return simulator.output().model_dump()


# ------------------------------------------------------------------ the prose

def test_the_reported_sentence_carries_no_identifiers(labels):
    prose, refs = humanise(REPORTED, labels)
    assert not IDENTIFIER.search(prose), f'an identifier survived: {prose}'
    assert 'Superstructure - structural steel frame' in prose
    assert 'Self-perform vs subcontract' in prose
    assert set(refs) == {
        'frag.superstructure.steel',
        'dp.dyn.low_confidence.frag.superstructure.steel',
        'dp.delivery_mode',
    }


def test_a_wrapping_decision_id_keeps_its_own_sense(labels):
    """"confirmed via Superstructure - structural steel frame" reads as nonsense.

    The confirmation came from the QUESTION ASKED ABOUT that package, and the sentence has to
    say so - otherwise removing the identifier has made the prose wrong rather than readable.
    """
    prose, _ = humanise(REPORTED, labels)
    assert 'the low-confidence question about Superstructure' in prose


def test_the_label_is_not_repeated_when_it_replaces_its_own_gloss(labels):
    """The engine wrote `frag.x (Name of x)`. Rewriting the id must not yield `Name (Name)`."""
    prose, _ = humanise('Instanced from frag.mep.cooling (MEP - cooling plant), because.', labels)
    assert prose.count('MEP - cooling plant') == 1, prose


def test_an_unknown_identifier_becomes_a_category_not_a_variable_name(labels):
    prose, refs = humanise('Gated by frag.nothing.here today.', labels)
    assert 'frag.nothing.here' not in prose
    assert 'a work package' in prose
    assert refs == ['frag.nothing.here']


def test_a_decision_point_is_named_by_its_title_not_its_question(labels):
    """A question is a whole sentence, and a sentence inside another sentence reads worse than
    the identifier it replaced."""
    prose, _ = humanise('See dp.delivery_mode for this.', labels)
    assert 'Self-perform vs subcontract (per discipline)' in prose
    assert '?' not in prose, f'a question was inlined: {prose}'


def test_every_trail_entry_in_a_real_run_is_free_of_identifiers(plan):
    entries = plan['reasoning_trail']
    assert entries
    for entry in entries:
        assert not IDENTIFIER.search(entry['why']), (
            f'{entry["ref_id"]} still shows an identifier: {entry["why"][:120]}'
        )


def test_the_identifiers_are_kept_not_discarded():
    """They move; they do not vanish. Whoever is debugging still needs them."""
    entries = [TrailEntry(ref_id='ref.1', stage='x', why=REPORTED, sources=[], confidence=0.5)]
    out = _readable_trail(entries)[0]
    assert out.technical_refs
    assert 'dp.delivery_mode' in out.technical_refs
    assert not IDENTIFIER.search(out.why)


def test_an_entrys_own_id_is_not_listed_as_a_reference_it_made():
    entry = TrailEntry(
        ref_id='frag.mep.cooling', stage='x', confidence=0.5, sources=[],
        why='Instanced from frag.mep.cooling because it applies.',
    )
    out = _readable_trail([entry])[0]
    assert 'frag.mep.cooling' not in out.technical_refs


def test_restored_runs_are_humanised_too():
    """A reopened run's trail arrives as plain dicts, and reaches the same screen."""
    out = _readable_trail([{'ref_id': 'ref.1', 'why': REPORTED}])[0]
    assert not IDENTIFIER.search(out['why'])
    assert out['technical_refs']


# ------------------------------------------------- what must NOT be touched

def test_wbs_codes_survive_untouched(plan):
    """Real industry notation. A planner reads `05.03.001` fluently."""
    coded = [a for a in plan['activities'] if re.fullmatch(r'\d\d\.\w\w\.\w+', str(a['wbs_id']))]
    assert len(coded) > 50, 'the WBS codes have changed shape'


def test_activity_names_stage_department_and_duration_are_untouched(plan):
    for activity in plan['activities']:
        assert activity['name'], 'an activity lost its name'
        assert 'a work package' not in activity['name'], (
            f'{activity["id"]}: the humaniser reached an activity name'
        )
    stages = {a['stage'] for a in plan['activities']}
    assert 'mep_power' in stages, 'stage labels have been rewritten'
    assert any(a['dept_code'] for a in plan['activities']), 'department labels were emptied'
    assert any(a['duration_days'] for a in plan['activities']), 'durations were emptied'


def test_sources_are_left_as_identifiers(plan):
    """The cited-sources list is a citation list, and a citation is an identifier by design."""
    cited = [s for entry in plan['reasoning_trail'] for s in entry['sources']]
    assert cited, 'nothing cited any source'
    assert any(IDENTIFIER.search(s) or ':' in s for s in cited), (
        'the humaniser reached the citation list, which must stay machine-readable'
    )
