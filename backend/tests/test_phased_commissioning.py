"""A phased handover commissions hall by hall; a single handover commissions once.

THE BRIEF DECIDES. Simultaneous commissioning is not a defect - build everything, commission the
facility as a unit, hand over once is a legitimate delivery model and correct for a project that
asks for it. What is wrong is applying either model regardless of what the brief says.

These briefs state phased hall-by-hall handover with staggered RFS dates, and a per-hall RFS date
is meaningless without per-hall commissioning: you cannot declare a hall ready for service without
having commissioned it. The L1-L5 ladder is what makes an RFS date real.

Two things had to be true and only one was. The ladder carries no `zone_kind`, so it instances
once for the campus. And `fit_out_complete` carries no `release_per_zone_kind`, so even a
zone-instanced ladder would hang off a campus-wide gate pinned to the LAST hall's fit-out - every
hall commissioning on the same day, which is per-hall structure that is not per-hall delivery.
Nine ladders labelled by hall that all finish together LOOK like phased commissioning while being
exactly what they replaced.
"""

from __future__ import annotations

import collections

import pytest

from backend.app.engine.schedule import energisation_days
from backend.app.llm_stub import StubAdapter
from backend.app.simulator import DecisionAnswer, Simulator

PHASED = {
    'project_name': 'Phased', 'city': 'Navi Mumbai', 'tier': 'III',
    'it_load_mw': 36.0, 'redundancy_topology': 'N+1', 'site_context': 'greenfield',
    'data_hall_count': 4, 'hall_handover_interval_days': 90,
}
SINGLE = {k: v for k, v in PHASED.items() if k != 'hall_handover_interval_days'}


def drive(brief):
    simulator = Simulator(dict(brief), adapter=StubAdapter())
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


@pytest.fixture(scope='module')
def phased():
    return drive(PHASED)


@pytest.fixture(scope='module')
def single():
    return drive(SINGLE)


def commissioning_by_hall(output):
    per = collections.defaultdict(list)
    for activity in output.activities:
        zone = activity.get('zone_id') or ''
        if 'data-hall' in zone and activity.get('stage') == 'commissioning' \
                and not activity.get('zone_inferred'):
            per[zone].append(activity)
    return per


def test_every_hall_is_commissioned_when_the_brief_phases_handover(phased):
    halls = {a['zone_id'] for a in phased.activities
             if a.get('zone_id') and 'data-hall' in a['zone_id']}
    commissioned = commissioning_by_hall(phased)
    assert set(commissioned) == halls, (
        f'{len(commissioned)} of {len(halls)} halls are commissioned; the rest have a '
        'ready-for-service date nothing earns'
    )


def test_a_hall_waits_for_its_own_fit_out_not_the_last_halls(phased):
    """The gate-pairing claim. Hall 2 commissioning after hall 4's fit-out is the phasing being
    honoured in the release and thrown away in the result."""
    by_hall = commissioning_by_hall(phased)
    fit_out_end = {}
    for activity in phased.activities:
        zone = activity.get('zone_id') or ''
        if 'data-hall' in zone and activity.get('stage') == 'fit_out':
            fit_out_end[zone] = max(fit_out_end.get(zone, 0), int(activity['finish_day']))

    last_fit_out = max(fit_out_end.values())
    for zone, activities in sorted(by_hall.items()):
        start = min(int(a['start_day']) for a in activities)
        own = fit_out_end.get(zone, 0)
        assert start < last_fit_out or own == last_fit_out, (
            f'{zone} commissioning starts on day {start}, after the campus fit-out ends '
            f'({last_fit_out}), though its own fit-out finished on {own}'
        )


def test_the_halls_energise_at_the_stated_interval(phased):
    """The inverted test. Part B recorded that no hall had an energisation date; now every hall
    has one and they must be spread by the interval the brief states, or the staggered dates are
    decorative."""
    days = sorted(energisation_days(phased.activities).values())
    assert len(days) >= 2, f'fewer than two halls energise: {days}'
    gaps = [b - a for a, b in zip(days, days[1:]) if b != a]
    assert gaps, f'every hall energises on the same day ({days[0]}), so nothing is phased'
    assert min(gaps) >= PHASED['hall_handover_interval_days'] * 0.5, (
        f'halls energise {gaps} days apart against a stated {PHASED["hall_handover_interval_days"]}'
    )


def test_a_single_handover_project_keeps_campus_commissioning(single):
    """The other half of "the brief decides". Forcing per-hall structure onto a project that
    hands over once would break it the same way campus-only breaks a phased one."""
    per_hall = commissioning_by_hall(single)
    assert not per_hall, (
        f'a single-handover brief was given per-hall commissioning for {sorted(per_hall)}'
    )
    assert any(a.get('stage') == 'commissioning' for a in single.activities), (
        'a single-handover brief lost its commissioning entirely'
    )


def test_a_halls_gate_is_fed_by_that_hall_and_not_by_its_neighbours(phased):
    """THE WIRING, not the ordering.

    Added because a mutation survived. Making each hall's gate wait on EVERY hall's fit-out
    instead of its own still produced a plan where halls commission in order - the whole schedule
    slid later together, and an assertion about ordering could not see it. Ordering is a
    consequence; the predecessor list is the thing that was changed.

    A hall's release gate may be fed by that hall's own work and by campus-wide work. A
    predecessor carrying a DIFFERENT hall's zone is the cartesian join this pairing exists to
    prevent: it turns parallel hall fit-outs back into one serial chain.
    """
    by_id = {a['id']: a for a in phased.activities}
    gates = [
        a for a in phased.activities
        if str(a.get('id', '')).startswith('gate.fit-out-complete.z')
    ]
    assert len(gates) > 1, (
        f'the fit-out gate was not instanced per hall: {[g["id"] for g in gates]}'
    )

    for gate in gates:
        gate_zone = gate.get('zone_id')
        assert gate_zone, f'{gate["id"]} carries no zone'
        foreign = []
        for pred in gate.get('predecessors') or []:
            # A sibling GATE is allowed and deliberate: the brief's handover interval is wired as
            # start-to-start from the first hall's gate, so hall i cannot be released before its
            # phased slot. That is the client commitment, not a dependency on another hall's work.
            if str(pred['id']).startswith('gate.'):
                continue
            producer = by_id.get(pred['id'])
            zone = producer.get('zone_id') if producer else None
            if zone and zone != gate_zone and 'data-hall' in str(zone):
                foreign.append((pred['id'], zone))
        assert not foreign, (
            f'{gate["id"]} for {gate_zone} waits on work in other halls: {foreign[:3]}'
        )
