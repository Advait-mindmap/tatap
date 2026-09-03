"""Earliest-start scheduling: turn logic and durations into a timeline.

Task 14 needs a timeline to scrub along, and until now nothing produced one. Activities carried
`duration_days` and `predecessors` but no dates, so "project start to RFS" had no meaning.

**This belongs in the engine, not the view** (CLAUDE.md rule 2): the deterministic engine
instances the activities, logic, durations *and dates*. A forward pass in the frontend would be
the view inventing a schedule, and two views would then disagree about when anything happens.

What this is: a forward pass over the precedence graph producing the earliest day each activity
can start and finish, in whole days from day 0. It invents nothing — every number is a sum of
durations and lags already in the plan.

What this is NOT: a full CPM. There is no backward pass, no float, no critical path, no calendar
arithmetic (a '6day' calendar is recorded on each activity but not yet applied), and no mapping
onto real dates. Those belong with the P6 export, which is where the spec puts them. Day offsets
are the honest unit for a scrubber: they are exactly as precise as the data supports.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence, Tuple

#: Relationship types the forward pass understands, as emitted by the fragnet library.
#: FS is the default; anything unrecognised is treated as FS, which is the conservative reading
#: (the successor waits for the predecessor) rather than silently allowing an overlap.
FINISH_TO_START = 'FS'
START_TO_START = 'SS'
FINISH_TO_FINISH = 'FF'
START_TO_FINISH = 'SF'


def _order(activities: Sequence[Any]) -> List[str]:
    """Topological order of activity ids, with cycles broken deterministically.

    A cycle in the logic is a data fault, not something to crash on: the walk still has to
    produce a timeline a planner can look at. Any activity left unresolved is appended in its
    original order, so the result stays deterministic and the cycle shows up as an activity
    that starts earlier than its predecessor rather than as an exception.
    """
    ids = [a.id for a in activities]
    index = {a.id: a for a in activities}
    remaining = dict.fromkeys(ids)
    resolved: List[str] = []
    done: set = set()

    # Iterate to a fixed point rather than recursing: plans are wide and shallow, and recursion
    # would risk a stack overflow on a large programme for no benefit.
    progress = True
    while remaining and progress:
        progress = False
        for activity_id in list(remaining):
            predecessors = [
                p.get('id') for p in (index[activity_id].predecessors or []) if p.get('id')
            ]
            if all(p in done or p not in index for p in predecessors):
                resolved.append(activity_id)
                done.add(activity_id)
                del remaining[activity_id]
                progress = True

    resolved.extend(remaining)  # whatever a cycle left behind, in stable order
    return resolved


def compute_schedule(activities: Sequence[Any]) -> Dict[str, Tuple[int, int]]:
    """Earliest (start_day, finish_day) per activity id, in whole days from day 0.

    Both ends are inclusive of the work: an activity starting on day 3 with a duration of 2 runs
    days 3 and 4 and finishes on day 5, so `finish = start + duration`. A milestone has zero
    duration and therefore starts and finishes on the same day, which is what a milestone means.
    """
    index = {a.id: a for a in activities}
    schedule: Dict[str, Tuple[int, int]] = {}

    for activity_id in _order(activities):
        activity = index[activity_id]
        duration = max(0, int(activity.duration_days or 0))
        start = 0

        for link in activity.predecessors or []:
            predecessor_id = link.get('id')
            if predecessor_id not in schedule:
                continue  # unknown or cyclic: constrains nothing rather than guessing
            p_start, p_finish = schedule[predecessor_id]
            lag = int(link.get('lag') or 0)
            kind = (link.get('type') or FINISH_TO_START).upper()

            if kind == START_TO_START:
                earliest = p_start + lag
            elif kind == FINISH_TO_FINISH:
                earliest = p_finish + lag - duration
            elif kind == START_TO_FINISH:
                earliest = p_start + lag - duration
            else:  # FS, and anything unrecognised
                earliest = p_finish + lag

            start = max(start, earliest)

        start = max(0, start)  # a negative lag must not push work before the project starts
        schedule[activity_id] = (start, start + duration)

    return schedule


def apply_schedule(activities: Sequence[Any]) -> int:
    """Write start_day/finish_day onto each activity. Returns the RFS day (the last finish).

    Mutating in place keeps this a projection of the assembly rather than a parallel structure
    that could drift from it.
    """
    schedule = compute_schedule(activities)
    for activity in activities:
        start, finish = schedule.get(activity.id, (0, 0))
        activity.start_day = start
        activity.finish_day = finish
    return max((f for _, f in schedule.values()), default=0)


def zone_timeline(activities: Iterable[Any]) -> Dict[str, Dict[str, Any]]:
    """When each zone comes into existence and what is happening in it, day by day.

    A zone appears on the canvas when the first activity that touches it starts, and its state
    is the stage of the work in progress there. That is what makes the 4D view mean something:
    the model is not a picture of the finished building shown early, it is what exists on that
    day.

    Returns, per zone_id: first_day, last_day, and the ordered stage spans within it.
    """
    spans: Dict[str, Dict[str, Any]] = {}

    for activity in activities:
        zone_id = getattr(activity, 'zone_id', None)
        if not zone_id:
            continue
        start = int(getattr(activity, 'start_day', 0) or 0)
        finish = int(getattr(activity, 'finish_day', 0) or 0)
        stage = getattr(activity, 'stage', '') or ''

        zone = spans.setdefault(
            zone_id, {'first_day': start, 'last_day': finish, 'stages': {}}
        )
        zone['first_day'] = min(zone['first_day'], start)
        zone['last_day'] = max(zone['last_day'], finish)

        stage_span = zone['stages'].setdefault(stage, {'from_day': start, 'to_day': finish})
        stage_span['from_day'] = min(stage_span['from_day'], start)
        stage_span['to_day'] = max(stage_span['to_day'], finish)

    # Stages ordered by when they begin, so a viewer can ask "what stage is this zone in on day
    # N" by walking the list rather than sorting it again.
    for zone in spans.values():
        zone['stages'] = [
            {'stage': stage, **span}
            for stage, span in sorted(
                zone['stages'].items(), key=lambda item: (item[1]['from_day'], item[0])
            )
        ]
    return spans


def stage_timeline(activities: Iterable[Any]) -> Dict[str, Dict[str, int]]:
    """When each stage runs: first start and last finish across its activities.

    Needed because most activities carry no `zone_id` — only a few are zone-specific — so a 4D
    model built solely from `zone_timeline` would leave most of the site never appearing. Each
    zone records the stage that brings it into existence (engine/zones.py ZONE_FIRST_STAGE), so
    the stage's span is what tells the model when to build it.

    Deriving zone appearance from its stage is an approximation and worth naming as one: it
    says "the data halls exist once superstructure starts", not "this hall was topped out on
    day 47". Sharper timing needs zone-tagged activities, which is a library change.
    """
    spans: Dict[str, Dict[str, int]] = {}
    for activity in activities:
        stage = getattr(activity, 'stage', '') or ''
        if not stage:
            continue
        start = int(getattr(activity, 'start_day', 0) or 0)
        finish = int(getattr(activity, 'finish_day', 0) or 0)
        span = spans.setdefault(stage, {'from_day': start, 'to_day': finish})
        span['from_day'] = min(span['from_day'], start)
        span['to_day'] = max(span['to_day'], finish)
    return spans
