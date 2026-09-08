"""Earliest-start scheduling: turn logic and durations into a timeline.

Task 14 needs a timeline to scrub along, and until now nothing produced one. Activities carried
`duration_days` and `predecessors` but no dates, so "project start to RFS" had no meaning.

**This belongs in the engine, not the view** (CLAUDE.md rule 2): the deterministic engine
instances the activities, logic, durations *and dates*. A forward pass in the frontend would be
the view inventing a schedule, and two views would then disagree about when anything happens.

What this is: a forward pass over the precedence graph producing the earliest day each activity
can start and finish, in whole days from day 0. It invents nothing — every number is a sum of
durations and lags already in the plan.

`compute_float` adds the backward pass: late dates, total and free float, and which activities
are critical. Same rule as the forward pass - every number is a sum of durations and lags already
in the plan, and nothing is estimated.

What this is still NOT: calendar arithmetic. A '6day' calendar is recorded on each activity and
the export now carries it, but the passes here count whole days, so P6 recalculating on F9 can
land on different dates than we wrote. Float is computed on the same day count as the dates it
describes, which keeps the two consistent with each other until the calendars are applied.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

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



#: The stage after which a zone carries live load. A hall is not energised because its walls are
#: up or its gear is delivered; it is energised when its commissioning completes.
ENERGISING_STAGE = 'commissioning'


def energisation_days(activities: Iterable[Any]) -> Dict[str, int]:
    """The day each zone goes live, for the zones whose plan establishes one.

    Part of replacing name-keyword safety matching with a real condition: a concurrent-operations
    control applies to work beside an ENERGISED hall, and that requires knowing when each hall
    becomes live. Every day returned is a finish day the forward pass computed for real
    commissioning activities in that zone - nothing here is assumed, and the phased-handover logic
    already spreads those dates per hall on a phased brief.

    A ZONE WITH NO COMMISSIONING WORK IS ABSENT FROM THE RESULT, deliberately. The plan does not
    say when it energises, and both available guesses are dangerous in opposite directions: day
    zero invents a live hazard beside every activity, and never-live silently removes controls
    that a real site needs. Absence is the one answer a caller can act on, and callers are
    expected to treat it as a reason to stop rather than a reason to proceed.
    """
    days: Dict[str, int] = {}
    for activity in activities:
        zone_id = _field(activity, 'zone_id')
        if not zone_id or _field(activity, 'stage') != ENERGISING_STAGE:
            continue
        # An INFERRED zone is a drawing position, not a place work happens. Reading one as real
        # is how this function first reported that hall 1 energised on day 960 when the campus
        # commissioning that produced the date was never hall 1's at all.
        if _field(activity, 'zone_inferred'):
            continue
        finish = int(_field(activity, 'finish_day') or 0)
        days[zone_id] = max(days.get(zone_id, finish), finish)
    return days


def is_energised(zone_id: str, day: int, days: Dict[str, int]) -> Optional[bool]:
    """Is this zone live on this day? None where the plan does not establish it.

    None rather than False: False reads as "safe to work beside", which is exactly the wrong
    thing to say about a hall whose energisation date nobody has established.

    A zone energised on day N is live ON day N. The boundary matters - it decides whether the
    work happening that day is concurrent-operations work or ordinary construction.
    """
    if zone_id not in days:
        return None
    return int(day) >= days[zone_id]


def _field(activity: Any, name: str) -> Any:
    """Read a field from either an object or a dict - callers have both."""
    if isinstance(activity, dict):
        return activity.get(name)
    return getattr(activity, name, None)


def compute_float(
    activities: Sequence[Any], schedule: Dict[str, Tuple[int, int]]
) -> Dict[str, Dict[str, int]]:
    """The backward pass: late dates, total and free float, and what is critical.

    The mirror of `compute_schedule`. That walks forward taking each activity's earliest start
    from its predecessors; this walks backward taking each activity's latest finish from its
    successors. Total float is the slack between the two, and an activity with none of it is on
    the critical path by definition.

    Everything here is arithmetic over durations and lags already in the plan - nothing is
    estimated, exactly as in the forward pass.

    FLOAT IS MEASURED AGAINST THE PROJECT FINISH, not against each chain's own end. A plan with
    two independent chains finishing on different days has slack in the shorter one; hanging late
    dates off each chain's own last activity would report zero float everywhere and make the
    critical path the whole plan, which is the classic way this calculation goes quietly wrong.

    NOT CALENDAR-AWARE, deliberately, because the forward pass is not either. These are day
    offsets, and P6 recalculating with real calendars can land elsewhere. Float consistent with
    the dates we export is the honest thing to ship until the calendars are applied; float on one
    basis and dates on another would be worse than none.
    """
    index = {a.id: a for a in activities}
    order = _order(activities)

    successors: Dict[str, List[Dict[str, Any]]] = {ident: [] for ident in index}
    for activity in activities:
        for link in activity.predecessors or []:
            predecessor_id = link.get('id')
            if predecessor_id in successors:
                successors[predecessor_id].append({'to': activity.id, 'link': link})

    project_finish = max((finish for _, finish in schedule.values()), default=0)

    late: Dict[str, Tuple[int, int]] = {}
    for activity_id in reversed(order):
        activity = index[activity_id]
        duration = max(0, int(activity.duration_days or 0))
        # An activity with no successors can finish as late as the project does. This is what
        # anchors every chain to the same end rather than to its own.
        finish = project_finish

        for edge in successors.get(activity_id, ()):
            if edge['to'] not in late:
                continue  # unknown or cyclic: constrains nothing rather than guessing
            s_late_start, s_late_finish = late[edge['to']]
            lag = int(edge['link'].get('lag') or 0)
            kind = (edge['link'].get('type') or FINISH_TO_START).upper()

            # Each type inverted. The forward pass says when the successor may start given this
            # activity; these say how late this activity may finish given the successor.
            if kind == START_TO_START:
                latest = s_late_start - lag + duration
            elif kind == FINISH_TO_FINISH:
                latest = s_late_finish - lag
            elif kind == START_TO_FINISH:
                latest = s_late_start - lag + duration
            else:  # FS, and anything unrecognised - matching the forward pass
                latest = s_late_start - lag

            finish = min(finish, latest)

        late[activity_id] = (finish - duration, finish)

    result: Dict[str, Dict[str, int]] = {}
    for activity_id, (late_start, late_finish) in late.items():
        early_start, early_finish = schedule.get(activity_id, (0, 0))
        total = late_start - early_start

        # FREE float is what this activity can absorb without moving any SUCCESSOR, as opposed to
        # without moving the project. It is the gap to the earliest thing waiting on it, so an
        # activity nothing waits on has as much free float as it has total.
        gaps = []
        for edge in successors.get(activity_id, ()):
            if edge['to'] not in schedule:
                continue
            s_start, _ = schedule[edge['to']]
            lag = int(edge['link'].get('lag') or 0)
            gaps.append(s_start - lag - early_finish)
        free = min(gaps) if gaps else total

        result[activity_id] = {
            'late_start': late_start,
            'late_finish': late_finish,
            # Clamped at zero. A negative here means the logic cannot be satisfied - a cycle the
            # topological order broke, say - and reporting "minus three days of slack" as though
            # it were a measurement would be inventing a number to describe broken data.
            'total_float': max(0, total),
            'free_float': max(0, min(free, total)),
            'critical': total <= 0,
        }
    return result


def apply_float(activities: Sequence[Any]) -> Dict[str, Dict[str, int]]:
    """Compute float from the activities' own dates and return it by id."""
    return compute_float(activities, compute_schedule(activities))

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
