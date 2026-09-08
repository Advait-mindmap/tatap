"""Float, late dates and the critical path.

A P6 file with no float and no critical path is a dependency list with dates, not a schedule.
Measured before this: total_float_hr_cnt, free_float_hr_cnt, late_start_date, late_end_date and
float_path were blank on all 478 activities - and `driving_path_flag` was populated on all 478 with
"N", which is worse than blank. Blank says nothing is known; "N" asserts that no activity is on the
driving path, which is false on every schedule that has one.

The backward pass mirrors the forward one: walk in reverse topological order, take each activity's
late finish from its successors, and float is late start minus early start. Every number is
arithmetic over durations and lags already in the plan - nothing here is estimated.

WHAT IT DELIBERATELY IS NOT: calendar-aware. The forward pass counts whole days, so these are day
offsets converted at HOURS_PER_DAY, and P6 recalculating on F9 with real calendars can still land
elsewhere. That is the next piece of work. Float computed on this day count is consistent with the
dates we export, which is the honest thing to ship until the calendars are applied.
"""

from __future__ import annotations

import pytest

from backend.app.engine.schedule import compute_float, compute_schedule


def plan(*activities):
    class A:
        def __init__(self, ident, duration, preds):
            self.id = ident
            self.duration_days = duration
            self.predecessors = preds or []
            self.start_day = 0
            self.finish_day = 0

    return [A(i, d, p) for i, d, p in activities]


def test_a_single_chain_is_entirely_critical():
    """Nothing can slip without moving the end, so every activity has zero float."""
    acts = plan(('a', 5, []), ('b', 3, [{'id': 'a'}]), ('c', 2, [{'id': 'b'}]))
    floats = compute_float(acts, compute_schedule(acts))
    assert {i: f['total_float'] for i, f in floats.items()} == {'a': 0, 'b': 0, 'c': 0}
    assert all(f['critical'] for f in floats.values())


def test_a_short_parallel_branch_carries_the_slack():
    """THE POINT OF FLOAT. `b` takes 10 days and `c` takes 2, both feeding `d` - so `c` can slip
    8 days without moving anything, and `b` cannot slip at all."""
    acts = plan(
        ('a', 1, []),
        ('b', 10, [{'id': 'a'}]),
        ('c', 2, [{'id': 'a'}]),
        ('d', 1, [{'id': 'b'}, {'id': 'c'}]),
    )
    floats = compute_float(acts, compute_schedule(acts))
    assert floats['b']['total_float'] == 0, 'the long branch must be critical'
    assert floats['c']['total_float'] == 8, f'the short branch has 8 days of slack: {floats["c"]}'
    assert floats['a']['total_float'] == 0 and floats['d']['total_float'] == 0


def test_the_critical_path_is_the_chain_with_no_slack():
    acts = plan(
        ('a', 1, []),
        ('b', 10, [{'id': 'a'}]),
        ('c', 2, [{'id': 'a'}]),
        ('d', 1, [{'id': 'b'}, {'id': 'c'}]),
    )
    floats = compute_float(acts, compute_schedule(acts))
    critical = {i for i, f in floats.items() if f['critical']}
    assert critical == {'a', 'b', 'd'}, f'critical path reads {sorted(critical)}'


def test_late_dates_never_precede_early_dates():
    """An invariant that catches an inverted relationship faster than any example: late start is
    early start plus float, so it can never be earlier."""
    acts = plan(
        ('a', 3, []), ('b', 4, [{'id': 'a'}]), ('c', 1, [{'id': 'a'}]),
        ('d', 2, [{'id': 'b'}, {'id': 'c'}]), ('e', 6, [{'id': 'a'}]),
    )
    schedule = compute_schedule(acts)
    floats = compute_float(acts, schedule)
    for ident, (early_start, early_finish) in schedule.items():
        assert floats[ident]['late_start'] >= early_start, ident
        assert floats[ident]['late_finish'] >= early_finish, ident


def test_free_float_never_exceeds_total_float():
    """Free float is what an activity can absorb without moving its SUCCESSORS; total float is
    without moving the PROJECT. The first can never be larger than the second."""
    acts = plan(
        ('a', 2, []), ('b', 3, [{'id': 'a'}]), ('c', 9, [{'id': 'a'}]),
        ('d', 1, [{'id': 'b'}]), ('e', 1, [{'id': 'c'}, {'id': 'd'}]),
    )
    floats = compute_float(acts, compute_schedule(acts))
    for ident, f in floats.items():
        assert 0 <= f['free_float'] <= f['total_float'], (ident, f)


def test_a_lag_is_carried_into_the_float():
    """A 5-day lag is 5 days the successor cannot use, so the slack shrinks by exactly that."""
    without = plan(('a', 1, []), ('b', 2, [{'id': 'a'}]), ('c', 10, [{'id': 'a'}]),
                   ('d', 1, [{'id': 'b'}, {'id': 'c'}]))
    with_lag = plan(('a', 1, []), ('b', 2, [{'id': 'a', 'lag': 5}]), ('c', 10, [{'id': 'a'}]),
                    ('d', 1, [{'id': 'b'}, {'id': 'c'}]))
    a = compute_float(without, compute_schedule(without))['b']['total_float']
    b = compute_float(with_lag, compute_schedule(with_lag))['b']['total_float']
    assert b == a - 5, f'lag was not carried into float: {a} -> {b}'


def test_every_relationship_type_survives_the_backward_pass():
    """SS, FF and SF invert differently from FS, and getting one backwards produces float that
    looks plausible and is wrong. Asserted as an invariant rather than by example."""
    for kind in ('FS', 'SS', 'FF', 'SF'):
        acts = plan(('a', 4, []), ('b', 3, [{'id': 'a', 'type': kind}]), ('c', 1, [{'id': 'b'}]))
        schedule = compute_schedule(acts)
        floats = compute_float(acts, schedule)
        for ident, (early_start, early_finish) in schedule.items():
            assert floats[ident]['late_start'] >= early_start, (kind, ident)
            assert floats[ident]['late_finish'] >= early_finish, (kind, ident)
        assert any(f['critical'] for f in floats.values()), f'{kind}: nothing is critical'


def test_a_plan_with_several_ends_measures_float_against_the_last_one():
    """Two chains finishing on different days. Float is slack against the PROJECT finish, so the
    shorter chain has some and the longer has none - if late dates hung off each chain's own end,
    both would read zero and the critical path would be everything."""
    acts = plan(('a', 10, []), ('b', 2, []))
    floats = compute_float(acts, compute_schedule(acts))
    assert floats['a']['total_float'] == 0
    assert floats['b']['total_float'] == 8, f'got {floats["b"]}'


def test_free_float_is_smaller_than_total_when_a_successor_follows_immediately():
    """Free float must be measured against the SUCCESSOR, not inherited from total float.

    Added because a mutation survived: returning total float as free float passed every test here,
    since the earlier cases happen to have equal values. `b` can slip 8 days without moving the
    project, but not one day without moving `c`, which starts the moment it finishes - so free
    float is 0 and total is 8. A file reporting 8 for both tells a planner they have a week of
    room on an activity that has none.
    """
    acts = plan(
        ('a', 1, []),
        ('b', 2, [{'id': 'a'}]),
        ('c', 2, [{'id': 'b'}]),
        ('d', 12, [{'id': 'a'}]),
        ('e', 1, [{'id': 'c'}, {'id': 'd'}]),
    )
    floats = compute_float(acts, compute_schedule(acts))
    assert floats['b']['total_float'] == 8, floats['b']
    assert floats['b']['free_float'] == 0, (
        f"b's successor starts the day it finishes, so it has no free float: {floats['b']}"
    )


def test_a_finish_to_start_predecessor_is_bounded_by_its_successors_late_START():
    """FS says the successor begins after this one ends, so this one's latest finish is the
    successor's latest START, not its latest finish. Confusing the two hands every predecessor the
    successor's whole duration as extra float - plausible-looking and wrong."""
    acts = plan(('a', 4, []), ('b', 6, [{'id': 'a'}]))
    floats = compute_float(acts, compute_schedule(acts))
    assert floats['a']['late_finish'] == floats['b']['late_start'], (
        f"a must finish when b may start: {floats['a']} vs {floats['b']}"
    )
    assert floats['a']['total_float'] == 0
