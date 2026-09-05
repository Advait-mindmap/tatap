"""No stage may complete silently with nothing in it.

Found by audit on a completed 13-stage run: six stages (approvals, enabling, envelope,
fire_bms, fit_out, handover) produced zero activities, and four of the six raised no decision
point either. The walk reported all thirteen `completed`, and a planner reading the output got a
thirteen-stage programme with six stages missing and nothing anywhere saying so.

That is the exact failure CLAUDE.md rule 3 exists to prevent, and it was inconsistent as well as
wrong: every other under-specified thing in the reasoning loop stops and asks.

The important test here is `test_no_stage_in_a_full_walk_finishes_silently_empty` — it pins the
INVARIANT (a stage produces work or it says why not) rather than the mechanism, so it keeps
holding when a future stage loses its library coverage for some other reason.
"""

from __future__ import annotations

import collections

import pytest

from backend.app.libraries import load_library
from backend.app.llm_stub import StubAdapter
from backend.app.reasoning import gather_stage_libraries
from backend.app.reasoning.loop import build_stage_reasoning
from backend.app.reasoning.stages import STAGES
from backend.app.simulator import DecisionAnswer, Simulator

BRIEF = {
    'project_name': 'Coverage DC', 'city': 'Chennai', 'tier': 'IV',
    'it_load_mw': 30.0, 'redundancy_topology': '2N', 'site_context': 'brownfield',
}

#: Stages with no fragnets at the time of writing. Not a target to keep empty — the assertion
#: below is that they ASK, not that they stay uncovered. Adding a fragnet for one of these is a
#: fix, and the invariant test keeps passing when it happens.
EMPTY_RESPONSE = {'packages': [], 'gates': [], 'long_lead': [], 'decision_points': [], 'notes': ''}


def stages_with_fragnets():
    return {e['stage'] for e in load_library('fragnets')['entries']}


# ------------------------------------------------------------------- the invariant


@pytest.fixture(scope='module')
def full_walk():
    """One real 13-stage run, driven to completion, recording what each stage produced."""
    simulator = Simulator(BRIEF, run_id='coverage', adapter=StubAdapter())
    forks = collections.defaultdict(set)
    for _ in range(40):
        for event in simulator.run():
            if event.type == 'decision_needed':
                forks[event.stage].add(event.payload.get('id'))
        if not simulator.is_halted:
            break
        for fork in sorted(simulator.state.pending_decisions):
            simulator.answer(DecisionAnswer(decision_point_id=fork, answer='Proceed'))
    assert not simulator.is_halted, 'the walk never completed'

    output = simulator.output().model_dump()
    activities = collections.defaultdict(list)
    for activity in output['activities']:
        activities[activity['stage']].append(activity)
    return output, activities, forks


def test_no_stage_in_a_full_walk_finishes_silently_empty(full_walk):
    """Every stage either instances work or raises a decision point about why it did not.

    This is the whole fix, stated as a property. A stage that produces nothing and says nothing
    is a hole in the plan that looks like a completed stage.
    """
    output, activities, forks = full_walk
    assert output['project_meta']['stages_completed'] == list(STAGES), 'the walk was not full'

    silent = [
        stage for stage in STAGES
        if not activities.get(stage) and not forks.get(stage)
    ]
    assert not silent, (
        f'{len(silent)} stage(s) completed with no activities and no decision point: {silent}'
    )


def test_every_empty_stage_raises_its_own_coverage_fork(full_walk):
    """Specifically the coverage fork, not just any fork.

    approvals and enabling always raised curated forks about other things, which is how four
    genuinely silent stages hid behind two noisy ones for so long: counting forks per stage
    showed nothing wrong.
    """
    _, activities, forks = full_walk
    for stage in STAGES:
        if activities.get(stage):
            continue
        assert f'dyn.no_coverage.{stage}' in forks[stage], (
            f'{stage} produced no activities and raised no coverage fork'
        )


def test_the_coverage_fork_blocks(full_walk):
    """Non-blocking would be the same silence with extra steps."""
    _, activities, _ = full_walk
    stage = next(s for s in STAGES if not activities.get(s))
    reasoning = build_stage_reasoning(
        EMPTY_RESPONSE, stage=stage, libs=gather_stage_libraries(stage, 'Chennai'),
        hits=[], threshold=0.7,
    )
    fork = next(
        d for d in reasoning.decision_points
        if d.decision_point_id == f'dyn.no_coverage.{stage}'
    )
    assert fork.blocking is True
    assert fork.options, 'a fork with no options cannot be answered'


# ------------------------------------------- it distinguishes the two reasons for emptiness


def test_a_stage_with_no_library_coverage_says_so():
    stage = next(s for s in STAGES if s not in stages_with_fragnets())
    libs = gather_stage_libraries(stage, 'Chennai')
    assert not libs['fragnets'], f'{stage} has fragnets now; pick another for this test'

    reasoning = build_stage_reasoning(
        EMPTY_RESPONSE, stage=stage, libs=libs, hits=[], threshold=0.7
    )
    fork = next(d for d in reasoning.decision_points
                if d.decision_point_id == f'dyn.no_coverage.{stage}')
    assert 'no work packages' in fork.why_stuck
    assert 'gap in the library' in fork.why_stuck


def test_a_stage_that_had_packages_available_but_chose_none_says_that_instead():
    """A different failure with a different remedy: the library is fine, the reasoning missed."""
    stage = 'mep_power'
    libs = gather_stage_libraries(stage, 'Chennai')
    assert libs['fragnets'], 'mep_power lost its fragnets; this test needs a covered stage'

    reasoning = build_stage_reasoning(
        EMPTY_RESPONSE, stage=stage, libs=libs, hits=[], threshold=0.7
    )
    fork = next(d for d in reasoning.decision_points
                if d.decision_point_id == f'dyn.no_coverage.{stage}')
    assert 'selected none' in fork.why_stuck
    assert 'gap in the library' not in fork.why_stuck


def test_a_stage_that_instanced_work_raises_no_coverage_fork():
    """The fork must not fire on a healthy stage, or it becomes noise to click through."""
    stage = 'mep_power'
    libs = gather_stage_libraries(stage, 'Chennai')
    response = {
        'packages': [{'fragnet_id': 'frag.mep.power_train', 'why': 'applies',
                      'confidence': 0.9, 'sources': ['frag.mep.power_train']}],
        'gates': [], 'long_lead': [], 'decision_points': [], 'notes': '',
    }
    reasoning = build_stage_reasoning(
        response, stage=stage, libs=libs, hits=[], threshold=0.7
    )
    assert not [d for d in reasoning.decision_points
                if d.decision_point_id.startswith('dyn.no_coverage')]


# ------------------------------------------------------- the gap is legible in the output


def test_the_gap_is_flagged_not_only_asked(full_walk):
    """A planner reading the finished plan must see the hole without replaying the decisions."""
    output, activities, _ = full_walk
    uncovered = [s for s in STAGES if not activities.get(s)]
    if not uncovered:
        pytest.skip('every stage is covered; nothing to flag')

    flagged = {
        ref
        for flag in output['flags'] if flag['kind'] == 'stage_not_covered'
        for ref in flag['refs']
    }
    assert set(uncovered) <= flagged, (
        f'uncovered stages with no flag: {set(uncovered) - flagged}'
    )


def test_the_answer_is_recorded_in_the_warning():
    """Answering 'out of scope' and answering 'in scope' must not read identically afterwards."""
    stage = next(s for s in STAGES if s not in stages_with_fragnets())
    libs = gather_stage_libraries(stage, 'Chennai')
    dyn_id = f'dyn.no_coverage.{stage}'

    def warning_for(answer):
        reasoning = build_stage_reasoning(
            EMPTY_RESPONSE, stage=stage, libs=libs, hits=[], threshold=0.7,
            decisions=[{'id': dyn_id, 'answer': answer}],
        )
        return next(w for w in reasoning.warnings if 'NO WORK PACKAGES' in w)

    out_of_scope = warning_for('Out of scope on this project - leave it out of the plan')
    in_scope = warning_for('In scope - record the plan as incomplete until this stage is covered')

    assert 'out of scope' in out_of_scope
    assert 'knowingly incomplete' in in_scope
    assert out_of_scope != in_scope


# ---------------------------------------------------- the three new fragnet libraries


@pytest.mark.parametrize('stage,fragnet_id', [
    ('envelope', 'frag.envelope.shell'),
    ('fire_bms', 'frag.fire_bms.detection_suppression'),
    ('fit_out', 'frag.fit_out.cabling'),
])
def test_the_new_stages_instance_real_work(full_walk, stage, fragnet_id):
    """Not just that the library entry parses — that the engine turns it into a plan."""
    _, activities, _ = full_walk
    rows = activities.get(stage, [])
    assert rows, f'{stage} still produces nothing'
    assert {r['source_fragnet'] for r in rows if r.get('source_fragnet')} == {fragnet_id}
    assert [r for r in rows if r['type'] == 'task'], f'{stage} has no actual work, only markers'
    assert all(r['duration_days'] > 0 for r in rows if r['type'] == 'task')
    assert [r for r in rows if r.get('predecessors')], f'{stage} activities have no logic'


@pytest.mark.parametrize('fragnet_id', [
    'frag.envelope.shell', 'frag.fire_bms.detection_suppression', 'frag.fit_out.cabling',
])
def test_the_new_entries_declare_themselves_unverified(fragnet_id):
    """CLAUDE.md: invented numbers must not launder into confident reasoning. These durations
    are industry estimates and every one of them is a candidate for correction."""
    entry = next(e for e in load_library('fragnets')['entries'] if e['id'] == fragnet_id)
    provenance = entry['provenance']
    assert provenance['origin'] == 'industry_estimate'
    assert provenance['verification_status'] == 'unverified'
    assert provenance['verified_by'] is None
    assert 'INVENTED FOR REVIEW' in provenance['note']
    assert 'JUDGEMENTS TO CHALLENGE' in provenance['note'], (
        'the note must name what a reviewer should argue with, not just disclaim generally'
    )


def test_the_new_entries_only_reference_things_that_exist():
    """A material link or safety ref pointing at nothing fails silently: the gate is simply
    never inserted and the plan understates the constraint."""
    leads = {e['id'] for e in load_library('equipment_lead_times')['entries']}
    safety = {e['id'] for e in load_library('safety_register')['entries']}
    new = ('frag.envelope.shell', 'frag.fire_bms.detection_suppression', 'frag.fit_out.cabling')

    for entry in load_library('fragnets')['entries']:
        if entry['id'] not in new:
            continue
        activity_ids = {a['id'] for a in entry['activities']}
        for link in entry.get('material_links', []):
            assert link['activity'] in activity_ids, f"{entry['id']}: link to unknown activity"
            assert link['requires_delivery_of'] in leads, (
                f"{entry['id']}: {link['requires_delivery_of']} is not in equipment_lead_times"
            )
        for ref in entry.get('safety_refs', []):
            assert ref in safety, f'{entry["id"]}: {ref} is not in the safety register'
        for link in entry['logic']:
            assert link['from'] in activity_ids and link['to'] in activity_ids, (
                f"{entry['id']}: logic link references an activity that does not exist"
            )
        for hold in entry.get('hold_points', []):
            assert hold['after'] in activity_ids, (
                f"{entry['id']}: hold point after an activity that does not exist"
            )


# ------------------------------------------------- the physical build sequence

#: The chain a reviewer checks first. Each must finish before the next starts.
BUILD_SEQUENCE = ['substructure', 'superstructure', 'envelope', 'fit_out']

#: Commissioning exercises installed plant, so every one of these must be in before it starts.
COMMISSIONING_NEEDS = ['mep_power', 'mep_cooling', 'fire_bms', 'fit_out']


def stage_span(activities, stage):
    rows = activities.get(stage) or []
    assert rows, f'{stage} instanced nothing, so its sequencing cannot be checked'
    return min(r['start_day'] for r in rows), max(r['finish_day'] for r in rows)


def test_construction_stages_are_sequenced_with_controlled_overlap(full_walk):
    """Found by audit: every construction stage started on the same day.

    With only the IFC release gate in place, design completion freed substructure,
    superstructure, envelope and fit-out simultaneously - steel erection before the foundations
    were poured, raised floor in a building that did not exist.

    The first fix was whole-stage finish-to-start, which was correct about the order and wrong
    about the cost: it charged the programme for work the next stage does not wait on (envelope
    waiting for fireproofing to steel; fit-out waiting for water-tightness CLOSE-OUT), and made
    fit-out the false long pole of the whole job. Stages now release on named milestones, so
    they OVERLAP - which is what real construction does and what this test has to allow.

    So the assertion is not "strictly serial" any more. It is: the order is right, the starts
    are distinct, and each stage waits for the specific work that actually releases it.
    """
    _, activities, _ = full_walk
    spans = {s: stage_span(activities, s) for s in BUILD_SEQUENCE}

    starts = [spans[s][0] for s in BUILD_SEQUENCE]
    assert starts == sorted(starts), f'construction stages start out of order: {spans}'
    assert len(set(starts)) == len(starts), (
        f'construction stages share a start day - the original bug: {spans}'
    )

    # Overlap is allowed but must be partial: no stage may start before its predecessor has,
    # and none may start on the predecessor's own start day.
    for earlier, later in zip(BUILD_SEQUENCE, BUILD_SEQUENCE[1:]):
        assert spans[later][0] > spans[earlier][0], (
            f'{later} starts on day {spans[later][0]}, not after {earlier} began on '
            f'{spans[earlier][0]}'
        )


def test_each_stage_waits_for_the_work_that_actually_releases_it(full_walk):
    """The partial-release rules, checked against the activities rather than the gate table.

    A rule that names milestones but resolves to nothing would silently fall back to the whole
    stage, which is the conservative behaviour this replaced - so the point is to prove the
    named activity is what the next stage is waiting on.
    """
    output, activities, _ = full_walk
    by_id = {a['id']: a for a in output['activities']}

    def activity_ending(stage, fragment):
        return next(
            a for a in output['activities']
            if a['stage'] == stage and fragment.lower() in a['name'].lower()
        )

    # Envelope follows the composite slab pour, NOT fireproofing to steel - which is interior
    # work on the frame that in practice runs while the envelope is being clad.
    slab = activity_ending('superstructure', 'composite slab pour')
    fireproofing = activity_ending('superstructure', 'fireproofing to steel')
    envelope_start = stage_span(activities, 'envelope')[0]
    assert envelope_start >= slab['finish_day']
    assert envelope_start < fireproofing['finish_day'], (
        'envelope is still waiting for fireproofing, so the partial release is not in effect'
    )

    # Fit-out follows halls being roofed and clad, not envelope close-out.
    cladding = activity_ending('envelope', 'external cladding')
    closeout = activity_ending('envelope', 'water-tightness testing')
    fit_out_start = stage_span(activities, 'fit_out')[0]
    assert fit_out_start >= cladding['start_day'], 'fit-out starts before cladding even begins'
    assert fit_out_start < closeout['finish_day'], (
        'fit-out is still waiting for envelope close-out, so it will remain the false long pole'
    )

    # And the release is staged across the data halls rather than waiting for the last one.
    assert fit_out_start < cladding['finish_day'], (
        'fit-out waits for ALL cladding — the per-hall staged release is not being applied'
    )


def test_commissioning_starts_after_everything_it_commissions(full_walk):
    """An L5 integrated systems test runs the facility under load. Doing that with no
    suppression installed, or in a hall with no cabling in it, is not something a commissioning
    agent would sign."""
    _, activities, _ = full_walk
    start = stage_span(activities, 'commissioning')[0]
    for stage in COMMISSIONING_NEEDS:
        finish = stage_span(activities, stage)[1]
        assert start >= finish, (
            f'commissioning starts on day {start} but {stage} runs until day {finish}'
        )


def test_each_sequencing_gate_actually_reaches_the_activities(full_walk):
    """A gate in the table that never becomes a predecessor constrains nothing.

    Checked on the activities rather than on the edge list: the edge existing and the activity
    carrying it are different things, and the forward pass only reads the latter.
    """
    _, activities, _ = full_walk
    # Envelope and MEP release from the superstructure differently: envelope on the frame
    # being up (the slab), MEP on the whole package. Two rules, two milestones.
    expected = {
        'superstructure': 'gate.substructure-complete',
        'envelope': 'gate.superstructure-frame-up',
        'mep_power': 'gate.superstructure-complete',
        'mep_cooling': 'gate.superstructure-complete',
        'fit_out': 'gate.envelope-weathertight',
    }
    for stage, gate in expected.items():
        rows = activities.get(stage) or []
        assert rows, f'{stage} instanced nothing'
        assert any(
            p['id'] == gate for r in rows for p in (r.get('predecessors') or [])
        ), f'{gate} is in the gate table but reaches no {stage} activity'


def test_commissioning_carries_all_four_readiness_gates(full_walk):
    _, activities, _ = full_walk
    rows = activities.get('commissioning') or []
    carried = {
        p['id'] for r in rows for p in (r.get('predecessors') or [])
        if p['id'].startswith('gate.')
    }
    for gate in ('gate.power-installed', 'gate.cooling-installed',
                 'gate.fire-installed', 'gate.fit-out-complete'):
        assert gate in carried, f'commissioning does not wait on {gate}'


def test_the_critical_path_is_procurement_led(full_walk):
    """DOMAIN_KNOWLEDGE.md section 4: long-lead plant usually drives RFS.

    This assertion has been true, then false, then true again, and the history is the point.

    It held until the construction stages were sequenced. Whole-stage finish-to-start then made
    fit-out finish AFTER the power train on every brief, so commissioning waited on raised floor
    and structured cabling rather than on the transformer - which no reviewer would accept for a
    data centre, and which was an artefact of the modelling rather than a finding about the job.
    Rather than assert something false, the test was rewritten to record that fit-out drove the
    plan and to say plainly that this was probably wrong.

    It is now true again, and for a reason rather than by tuning: stages release on the
    milestones the next stage actually waits for, and fit-out's per-hall work overlaps instead
    of running as one whole-building chain. So the claim is asserted again.
    """
    output, activities, _ = full_walk
    feeders = {stage: stage_span(activities, stage)[1] for stage in COMMISSIONING_NEEDS}
    driver = max(feeders, key=feeders.get)

    assert driver == 'mep_power', (
        f'commissioning is driven by {driver} (day {feeders[driver]}), not the power train. '
        f'Feeders: {feeders}'
    )
    assert feeders['fit_out'] < feeders['mep_power'], (
        f"fit-out finishes on day {feeders['fit_out']}, after the power train on "
        f"{feeders['mep_power']} — it is the long pole again"
    )

    # And the power train's own finish traces back to the transformer, which is what
    # "procurement-led" means. Without this the assertion above could be satisfied by MEP
    # simply being slow.
    delivery = next(
        a for a in output['activities'] if a['id'] == 'gate.delivery-lead-transformer-hv'
    )
    assert stage_span(activities, 'mep_power')[1] > delivery['finish_day']
    assert delivery['finish_day'] > stage_span(activities, 'superstructure')[1], (
        'the frame now lands after the transformer, so the power train is structure-led'
    )


def test_mep_waits_for_the_frame_but_delivery_still_drives_its_finish(full_walk):
    """Both halves matter, and they are easy to confuse.

    Gating MEP on the frame moves when the power train can START - plinths and pad preparation
    need structure to fix to. It does NOT move when the power train FINISHES, because the
    32-week transformer lands long after the frame is up, and everything from placement onward
    hangs off that delivery.

    So sequencing MEP does not change RFS on the briefs this library can produce, and that is
    the correct result rather than a missing effect: DOMAIN_KNOWLEDGE.md section 4 says long-lead
    plant drives RFS, and this is that claim being true. The test pins both halves so a future
    change cannot quietly break either - if the frame ever became the binding constraint, the
    plan would have stopped being procurement-led and someone should notice.
    """
    output, activities, _ = full_walk

    frame_end = stage_span(activities, 'superstructure')[1]
    mep_start, mep_end = stage_span(activities, 'mep_power')

    # The gate binds the start.
    assert mep_start >= frame_end, (
        f'mep_power starts on day {mep_start}, before the frame is up on day {frame_end}'
    )

    # Delivery binds the finish.
    delivery = next(
        a for a in output['activities'] if a['id'] == 'gate.delivery-lead-transformer-hv'
    )
    assert delivery['finish_day'] > frame_end, (
        f"the transformer lands on day {delivery['finish_day']}, before the frame is up on "
        f'{frame_end} — the frame is now the binding constraint and the plan is no longer '
        'procurement-led'
    )
    assert mep_end > delivery['finish_day'], (
        'the power train finishes before its transformer arrives'
    )
