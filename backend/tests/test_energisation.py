"""When does each zone go live, and when does the plan simply not say?

Part B of replacing name-keyword safety matching with a condition computed from real data. A
concurrent-operations control applies to work beside an ENERGISED hall, so the matcher needs two
facts: which zones neighbour which, and when each becomes live.

THE FIRST VERSION OF THIS FILE WAS BUILT ON AN ARTIFACT and its results are discarded. It read
`zone_id` from commissioning activities and reported that hall 1 energised on day 960. But the
commissioning ladder is instanced ONCE for the campus, and `_attach_zones` - a 4D display fallback
- had labelled all twenty of its activities `zone.data-hall.01` so the model had somewhere to draw
them. There was no hall-1 commissioning. The date was computed from a drawing position.

Hence `zone_inferred`: the fallback now says it guessed, and this function skips anything wearing
that mark. The contract is exercised against SYNTHETIC activities below, so it states what the
function guarantees rather than what one library configuration happens to produce - which is the
mistake that let the artifact through in the first place.
"""

from __future__ import annotations

import pytest

from backend.app.engine.schedule import ENERGISING_STAGE, energisation_days, is_energised
from backend.app.llm_stub import StubAdapter
from backend.app.simulator import DecisionAnswer, Simulator


def activity(ident, zone, stage, finish, inferred=False):
    return {
        'id': ident, 'zone_id': zone, 'stage': stage,
        'start_day': max(0, finish - 10), 'finish_day': finish, 'zone_inferred': inferred,
    }


# ------------------------------------------------------------------ the contract

def test_a_zone_energises_when_its_own_commissioning_finishes():
    days = energisation_days([
        activity('a', 'zone.data-hall.01', ENERGISING_STAGE, 100),
        activity('b', 'zone.data-hall.01', ENERGISING_STAGE, 140),
        activity('c', 'zone.data-hall.02', ENERGISING_STAGE, 230),
    ])
    assert days == {'zone.data-hall.01': 140, 'zone.data-hall.02': 230}


def test_a_zone_placed_by_the_display_fallback_establishes_nothing():
    """The exact fault this was rebuilt around: a drawn position is not a place work happens."""
    days = energisation_days([
        activity('camp', 'zone.data-hall.01', ENERGISING_STAGE, 960, inferred=True),
    ])
    assert days == {}, f'a display-placed activity was read as real commissioning: {days}'


def test_real_commissioning_still_counts_when_inferred_work_shares_the_zone():
    """One inferred neighbour must not poison a zone that genuinely commissions."""
    days = energisation_days([
        activity('camp', 'zone.data-hall.01', ENERGISING_STAGE, 960, inferred=True),
        activity('real', 'zone.data-hall.01', ENERGISING_STAGE, 300),
    ])
    assert days == {'zone.data-hall.01': 300}


def test_work_that_is_not_commissioning_never_energises_a_zone():
    assert energisation_days([activity('f', 'zone.data-hall.01', 'fit_out', 500)]) == {}


def test_an_unknown_zone_answers_unknown_not_false():
    """False reads as "safe to work beside" - the dangerous reading of missing data."""
    days = {'zone.data-hall.01': 100}
    assert is_energised('zone.data-hall.02', 9999, days) is None
    assert is_energised('zone.data-hall.01', 101, days) is True
    assert is_energised('zone.data-hall.01', 99, days) is False


def test_a_zone_is_live_on_the_day_it_energises():
    """Off-by-one here decides whether that day's work is concurrent-operations work."""
    assert is_energised('z', 100, {'z': 100}) is True


# ------------------------------------------------ what the product actually produces today

@pytest.fixture(scope='module')
def phased_run():
    brief = {
        'project_name': 'Energisation', 'city': 'Navi Mumbai', 'tier': 'III',
        'it_load_mw': 36.0, 'redundancy_topology': 'N+1', 'site_context': 'brownfield',
        'data_hall_count': 4, 'hall_handover_interval_days': 90,
    }
    simulator = Simulator(brief, adapter=StubAdapter())
    for _ in range(50):
        list(simulator.run())
        if not simulator.is_halted:
            break
        for fork in sorted(simulator.state.pending_decisions):
            payload = simulator.state.pending_decisions[fork]
            simulator.answer(DecisionAnswer(
                decision_point_id=fork, answer=(payload.get('options') or ['Confirmed'])[0],
            ))
    return simulator.output()


def test_no_hall_has_an_established_energisation_date_yet(phased_run):
    """THIS IS A RECORD OF A GAP, NOT AN ENDORSEMENT OF IT.

    The commissioning ladder is instanced once for the campus, so on a four-hall phased brief no
    hall has commissioning of its own and no hall has an energisation date. Making the ladder
    per-hall needs the gate feeding it to pair per zone as well - without that, every hall waits
    on a campus-wide fit-out gate pinned to the LAST hall and they all energise on the same day,
    which breaks the stated handover interval.

    When that lands this test must be inverted, and the assertion says so rather than quietly
    passing on the wrong answer.
    """
    days = energisation_days(phased_run.activities)
    halls = {a.get('zone_id') for a in phased_run.activities
             if a.get('zone_id') and 'data-hall' in a['zone_id']}
    assert len(halls) == 4, f'expected four hall zones, found {sorted(halls)}'
    assert days == {}, (
        'a hall now has a real energisation date - per-hall commissioning has landed, so invert '
        f'this test and assert the phasing interval between halls instead: {days}'
    )
