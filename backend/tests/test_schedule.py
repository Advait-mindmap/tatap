"""The engine's forward pass: logic and durations become a timeline.

Task 14's scrubber runs from project start to RFS, which requires the plan to be scheduled at
all. These tests pin the arithmetic and, more importantly, pin that the schedule OBEYS THE
LOGIC — a 4D model that shows steel going up before the foundations is worse than no 4D model,
because it looks authoritative while being wrong.
"""

from __future__ import annotations

from backend.app.engine.schedule import apply_schedule, compute_schedule, zone_timeline
from backend.app.schemas import AssembledActivity


def act(id_, duration=0, preds=None, stage='', zone=None):
    return AssembledActivity(
        id=id_, wbs_id=id_, name=id_, duration_days=duration, stage=stage, zone_id=zone,
        predecessors=preds or [],
    )


def link(id_, type_='FS', lag=0):
    return {'id': id_, 'type': type_, 'lag': lag, 'kind': 'fragnet'}


# ------------------------------------------------------------------------------- arithmetic

def test_an_activity_with_no_predecessors_starts_on_day_zero():
    schedule = compute_schedule([act('a', 5)])
    assert schedule['a'] == (0, 5)


def test_finish_to_start_puts_the_successor_after_the_predecessor():
    schedule = compute_schedule([act('a', 5), act('b', 3, [link('a')])])
    assert schedule['a'] == (0, 5)
    assert schedule['b'] == (5, 8)


def test_a_milestone_starts_and_finishes_on_the_same_day():
    """Zero duration is what a milestone means; it must not consume a day."""
    schedule = compute_schedule([act('a', 4), act('m', 0, [link('a')])])
    assert schedule['m'] == (4, 4)


def test_lag_delays_the_successor():
    schedule = compute_schedule([act('a', 5), act('b', 2, [link('a', lag=10)])])
    assert schedule['b'] == (15, 17)


def test_the_latest_predecessor_governs():
    """A join waits for its slowest input, not its first."""
    schedule = compute_schedule([
        act('a', 5), act('b', 20), act('c', 1, [link('a'), link('b')]),
    ])
    assert schedule['c'] == (20, 21)


def test_start_to_start_lets_work_overlap():
    schedule = compute_schedule([act('a', 10), act('b', 4, [link('a', 'SS', lag=2)])])
    assert schedule['b'] == (2, 6)


def test_finish_to_finish_aligns_the_ends():
    schedule = compute_schedule([act('a', 10), act('b', 4, [link('a', 'FF')])])
    assert schedule['b'] == (6, 10)


def test_an_unknown_relationship_is_treated_as_finish_to_start():
    """The conservative reading. Silently allowing an overlap would invent concurrency."""
    schedule = compute_schedule([act('a', 5), act('b', 2, [link('a', 'WAT')])])
    assert schedule['b'] == (5, 7)


def test_nothing_is_scheduled_before_the_project_starts():
    """A negative lag must not produce work on day -3."""
    schedule = compute_schedule([act('a', 2), act('b', 1, [link('a', lag=-99)])])
    assert schedule['b'][0] >= 0


# --------------------------------------------------------------------------------- ordering

def test_the_pass_is_order_independent():
    """The same plan listed in any order must schedule identically.

    Activities arrive sorted by stage and WBS, not in dependency order, so a pass that only
    worked when predecessors happened to come first would be right by luck.
    """
    forward = [act('a', 5), act('b', 3, [link('a')]), act('c', 2, [link('b')])]
    backward = [act('c', 2, [link('b')]), act('b', 3, [link('a')]), act('a', 5)]
    assert compute_schedule(forward) == compute_schedule(backward)


def test_a_cycle_does_not_crash_the_walk():
    """Bad logic is a data fault. The planner still needs a timeline to look at."""
    schedule = compute_schedule([
        act('a', 2, [link('b')]), act('b', 2, [link('a')]), act('c', 3),
    ])
    assert schedule['c'] == (0, 3)
    assert set(schedule) == {'a', 'b', 'c'}  # nothing dropped


def test_an_unknown_predecessor_constrains_nothing():
    schedule = compute_schedule([act('b', 4, [link('does-not-exist')])])
    assert schedule['b'] == (0, 4)


def test_the_schedule_is_deterministic():
    plan = [act('a', 5), act('b', 3, [link('a')]), act('c', 7, [link('a', 'SS', lag=1)])]
    assert compute_schedule(plan) == compute_schedule(plan)


# ------------------------------------------------------------------------------- application

def test_apply_schedule_writes_days_and_returns_rfs():
    plan = [act('a', 5), act('b', 3, [link('a')])]
    rfs = apply_schedule(plan)
    assert (plan[0].start_day, plan[0].finish_day) == (0, 5)
    assert (plan[1].start_day, plan[1].finish_day) == (5, 8)
    assert rfs == 8, 'RFS is the last finish across the plan'


def test_rfs_of_an_empty_plan_is_zero():
    assert apply_schedule([]) == 0


# ------------------------------------------------------------------------------ zone spans

def test_a_zone_exists_from_its_first_activity_to_its_last():
    plan = [
        act('a', 5, stage='substructure', zone='zone.hall.01'),
        act('b', 3, [link('a')], stage='mep_power', zone='zone.hall.01'),
    ]
    apply_schedule(plan)
    spans = zone_timeline(plan)

    assert spans['zone.hall.01']['first_day'] == 0
    assert spans['zone.hall.01']['last_day'] == 8


def test_zone_stages_are_ordered_by_when_they_begin():
    """The 4D model walks this list to answer "what stage is this zone in on day N"."""
    plan = [
        act('late', 2, stage='commissioning', zone='z'),
        act('early', 4, stage='substructure', zone='z'),
    ]
    plan[0].predecessors = [link('early')]
    apply_schedule(plan)
    stages = [s['stage'] for s in zone_timeline(plan)['z']['stages']]
    assert stages == ['substructure', 'commissioning']


def test_activities_without_a_zone_are_not_invented_into_one():
    plan = [act('a', 5, stage='approvals', zone=None)]
    apply_schedule(plan)
    assert zone_timeline(plan) == {}


# --------------------------------------------------------------------- against the real plan

def test_the_real_assembly_schedules_every_activity_after_its_predecessors():
    """The guarantee that matters, checked on the golden plan rather than a toy.

    If this fails the 4D model would show work happening before the work it depends on.
    """
    import io
    import json
    import pathlib

    golden = pathlib.Path(__file__).parent / 'golden' / 'simulation_output.json'
    data = json.load(io.open(golden, encoding='utf-8'))
    by_id = {a['id']: a for a in data['activities']}

    assert data['rfs_day'] > 0, 'the golden plan has no timeline'

    violations = []
    for activity in data['activities']:
        for pred in activity.get('predecessors') or []:
            predecessor = by_id.get(pred.get('id'))
            if not predecessor or (pred.get('type') or 'FS').upper() != 'FS':
                continue
            lag = int(pred.get('lag') or 0)
            if activity['start_day'] < predecessor['finish_day'] + lag:
                violations.append(
                    f"{activity['id']} starts day {activity['start_day']} but "
                    f"{predecessor['id']} finishes day {predecessor['finish_day']} (+{lag})"
                )

    assert not violations, 'schedule violates its own logic: ' + '; '.join(violations[:5])


def test_the_golden_plan_gives_the_scrubber_something_to_scrub():
    """A timeline where everything happens on day 0 would make the 4D view pointless."""
    import io
    import json
    import pathlib

    golden = pathlib.Path(__file__).parent / 'golden' / 'simulation_output.json'
    data = json.load(io.open(golden, encoding='utf-8'))

    starts = {a['start_day'] for a in data['activities']}
    assert len(starts) > 3, f'only {len(starts)} distinct start days in the whole plan'
    assert data['zone_timeline'], 'no zone has a timeline, so nothing can appear over time'
