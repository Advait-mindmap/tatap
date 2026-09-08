"""A fork the reasoner declines to raise must still be raised.

THE PATTERN, found by auditing every curated decision point after the BMS regression:

  * a curated fork is ELECTIVE - it reaches the planner only if the model names it in its
    response (`detection='curated'`), which with a real LLM varies run to run;
  * the engine's fallback for the same question is UNCONDITIONAL - it applies whether or not the
    fork was ever asked.

Seven of the eight curated forks pair an elective question with a silent default. This file
covers the most serious one.

TIER-1 SAFETY CAN VANISH. `safety.live_hall_works` is tier_1, `blocks_export: true`, and gated on
`applies_when_site_context: brownfield`. A brief that does not state its site context leaves
`site_context` as '', the rule never matches, and the plan simply has no live-hall safety control
in it. The export gate does not catch this, because it keys on tier-1 activities BEING PRESENT -
there is nothing to sign off, so nothing blocks. A model that did not think to ask a question
removes a safety control and the gate that should catch it.

The tests drive a SILENT adapter - one that raises no curated decision points, exactly as the
production reasoner did on run-4b8db1e64b844916a010f2b7d979059c. Against the ordinary stub these
tests would pass without any fix, because the stub names every fork in the library.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from backend.app.llm_stub import StubAdapter
from backend.app.simulator import DecisionAnswer, Simulator

SITE_FORK = 'dp.greenfield_brownfield'


class SilentAdapter(StubAdapter):
    """The stub, with every curated decision point stripped from its reasoning responses.

    This is what the real reasoner did: selected packages, reported confidence, and simply did
    not raise the fork. Nothing else about the response changes, so anything these tests catch is
    the elective-firing pattern and not a broken stub.
    """

    def invoke(self, system: str = '', user: str = '', schema=None, **kwargs: Any) -> Dict[str, Any]:
        response = super().invoke(system=system, user=user, schema=schema, **kwargs)
        if isinstance(response, dict) and 'decision_points' in response:
            response = dict(response, decision_points=[])
        return response


BRIEF = {
    'project_name': 'Silent forks', 'city': 'Navi Mumbai', 'tier': 'III',
    'it_load_mw': 20.0, 'redundancy_topology': 'N+1',
    # site_context deliberately absent - this is the whole point.
}


def walk(simulator, answers=None, rounds=40):
    answers = answers or {}
    seen = set()
    for _ in range(rounds):
        list(simulator.run())
        seen |= set(simulator.state.pending_decisions)
        if not simulator.is_halted:
            break
        for fork in sorted(simulator.state.pending_decisions):
            payload = simulator.state.pending_decisions[fork]
            options = payload.get('options') or ['Confirmed']
            simulator.answer(DecisionAnswer(
                decision_point_id=fork, answer=answers.get(fork, options[0]),
            ))
    return seen


def test_a_brief_silent_on_site_context_is_still_asked():
    """The reasoner declined to ask. The engine must ask anyway."""
    simulator = Simulator(dict(BRIEF), adapter=SilentAdapter())
    seen = walk(simulator)
    assert SITE_FORK in seen, (
        'no fork asked whether this is a live facility, so the tier-1 live-hall safety rule '
        f'could never match. Forks raised: {sorted(seen)}'
    )


def test_answering_brownfield_reaches_the_brief():
    """A fork whose answer never lands is theatre - the defect this whole pass is about."""
    simulator = Simulator(dict(BRIEF), adapter=SilentAdapter())
    walk(simulator, answers={SITE_FORK: 'Brownfield - adjacent to live halls'})
    assert str(simulator.brief.get('site_context') or '').lower() == 'brownfield', (
        f'answer did not reach the brief: site_context={simulator.brief.get("site_context")!r}'
    )


def test_brownfield_puts_the_tier_one_safety_control_in_the_plan():
    """The consequence that matters: the safety control exists, and it blocks export."""
    simulator = Simulator(dict(BRIEF), adapter=SilentAdapter())
    walk(simulator, answers={SITE_FORK: 'Brownfield - adjacent to live halls'})
    activities = simulator.output().activities
    tier_one = [a for a in activities if str(a.get('hitl_tier') or '') == 'tier_1']
    blocking = [a for a in activities if a.get('blocks_export')]
    assert tier_one, 'a brownfield plan carries no tier-1 safety activity at all'
    assert blocking, 'nothing in a brownfield plan blocks export for sign-off'


def test_a_brief_that_states_its_site_context_is_not_asked():
    """Stated content is never re-litigated; asking risks overwriting a verified field."""
    simulator = Simulator(dict(BRIEF, site_context='greenfield'), adapter=SilentAdapter())
    seen = walk(simulator)
    assert SITE_FORK not in seen


def test_an_answer_naming_no_context_writes_nothing():
    """An unclear answer must leave the brief silent, not default to greenfield.

    Added because a mutation survived: `read_site_context` returning 'greenfield' instead of None
    for an unrecognised answer passed every other test in this file. That default is the whole
    fault in miniature - greenfield is the reading under which the tier-1 live-hall rule does not
    apply, so guessing it drops the safety control exactly as silently as never asking did.
    """
    simulator = Simulator(dict(BRIEF), adapter=SilentAdapter())
    walk(simulator, answers={SITE_FORK: 'We are still confirming this with the client'})
    assert not str(simulator.brief.get('site_context') or '').strip(), (
        'an answer that named no site context was read as one: '
        f'site_context={simulator.brief.get("site_context")!r}'
    )
