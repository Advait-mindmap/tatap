"""When does each zone go live?

Part B of replacing name-keyword safety matching with a real condition. A concurrent-operations
control applies to work happening NEXT TO AN ENERGISED HALL, so the matcher needs two facts:
which zones neighbour which (Part A), and when each zone becomes live. This is the second.

The plan already contains the answer for any zone whose commissioning work is instanced: power is
live once that zone's commissioning completes, and the phased-handover logic already spreads those
dates per hall. Nothing here invents a date - every day returned is the finish day the forward
pass computed for real activities.

WHAT IT MUST NOT DO IS GUESS. A zone whose commissioning is not instanced has NO established
energisation date in the plan, and the honest answer is "unknown", not day zero (always live,
inventing hazards) and not infinity (never live, silently removing them). Both wrong answers look
like data; "unknown" is the one a caller can act on, and Part C treats it as a reason to stop
rather than a reason to proceed.
"""

from __future__ import annotations

import pytest

from backend.app.engine.schedule import energisation_days, is_energised
from backend.app.llm_stub import StubAdapter
from backend.app.simulator import DecisionAnswer, Simulator

#: Four halls, ninety days apart. The interval is what makes per-hall timing observable at all.
BRIEF = {
    'project_name': 'Energisation', 'city': 'Navi Mumbai', 'tier': 'III',
    'it_load_mw': 36.0, 'redundancy_topology': 'N+1', 'site_context': 'brownfield',
    'data_hall_count': 4, 'hall_handover_interval_days': 90,
}

#: The stage after which a zone carries live load.
ENERGISING_STAGE = 'commissioning'


@pytest.fixture(scope='module')
def output():
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
    return simulator.output()


def test_a_zones_energisation_is_the_finish_of_its_own_commissioning(output):
    """The specific claim, not an adjacent one: the day equals a real computed finish day."""
    days = energisation_days(output.activities)
    assert days, 'no zone in the plan has an established energisation date at all'

    for zone_id, day in days.items():
        finishes = [
            int(a['finish_day']) for a in output.activities
            if a.get('zone_id') == zone_id and a.get('stage') == ENERGISING_STAGE
        ]
        assert finishes, f'{zone_id} was given an energisation day with no commissioning work'
        assert day == max(finishes), (
            f'{zone_id} energises on {day} but its commissioning finishes on {max(finishes)}'
        )


def test_a_zone_the_plan_never_energises_is_absent_rather_than_assumed(output):
    """The failure mode this exists to prevent. A missing date must not become day 0."""
    days = energisation_days(output.activities)
    zoned = {a['zone_id'] for a in output.activities if a.get('zone_id')}
    without = {
        z for z in zoned
        if not any(a.get('zone_id') == z and a.get('stage') == ENERGISING_STAGE
                   for a in output.activities)
    }
    assert without, 'every zone commissions here, so this test proves nothing'
    for zone_id in without:
        assert zone_id not in days, (
            f'{zone_id} has no commissioning work but was assigned energisation day '
            f'{days.get(zone_id)}'
        )


def test_an_unknown_zone_answers_unknown_not_false(output):
    """False would mean "safe to work beside", which is the dangerous reading of missing data."""
    days = energisation_days(output.activities)
    assert is_energised('zone.does-not-exist', 9999, days) is None
    known = sorted(days)[0]
    assert is_energised(known, days[known] + 1, days) is True
    assert is_energised(known, days[known] - 1, days) is False


def test_energisation_is_reported_on_the_boundary_day(output):
    """A hall energised on day N is live on day N. Off-by-one here decides whether the work
    happening that day is concurrent-operations work or not."""
    days = energisation_days(output.activities)
    zone = sorted(days)[0]
    assert is_energised(zone, days[zone], days) is True


def test_phasing_separates_the_halls_that_do_commission(output):
    """Where two halls both commission, the stated interval must show up between them - otherwise
    the timing is not carrying the brief's phasing and every hall would look live at once."""
    days = energisation_days(output.activities)
    halls = sorted(d for z, d in days.items() if 'data-hall' in z)
    if len(halls) < 2:
        pytest.skip(f'only {len(halls)} data hall(s) commission in this plan')
    assert halls[1] - halls[0] >= BRIEF['hall_handover_interval_days'] * 0.5
