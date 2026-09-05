"""A slow run must not freeze the service.

Found against the deployment, not in any test. `ws_simulate` iterated a SYNCHRONOUS generator -

    for event in simulator.run():
        await websocket.send_json(...)

- and `simulator.run()` makes a blocking LLM call per stage. That froze the process's single
asyncio loop for the whole of each stage's reasoning. Three consequences, all observed live:

  * uvicorn could not service its own websocket keepalive and closed live runs with
    1011 "keepalive ping timeout" partway through;
  * GET /health - a route that returns a dict - timed out at 30 seconds during a walk and
    answered in 0.4 seconds once idle. One user's run made the whole service unavailable to
    everyone, and a platform health check hitting that window restarts the container, killing
    every in-flight run;
  * a run whose client had gone kept walking and kept blocking, so one abandoned socket
    degraded the service for minutes.

None of this could show up locally, because the stub answers instantly: the bug needs a slow
provider. So this test supplies one - a reasoner that sleeps - and asserts the loop stays
responsive while it does. It runs a REAL uvicorn server, because that is the only way to
exercise the actual event loop; TestClient drives the app through a portal and would pass
either way.
"""

from __future__ import annotations

import socket
import threading
import time
import urllib.request

import pytest

from backend.app.schemas import PackageSelection, StageReasoning

pytest.importorskip('uvicorn')
pytest.importorskip('websockets')

#: How long a stage "reasons" for. Long enough that a blocked loop is unambiguous, short enough
#: that the test stays quick. Base44 stages take longer than this in production.
STAGE_SECONDS = 3.0

#: A health check must return in well under a stage. Generous against CI scheduling noise while
#: still being far below STAGE_SECONDS - the bug made this take the full stage or longer.
HEALTH_BUDGET = 1.5


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _slow_reasoner(stage, brief, **kwargs):
    """Stands in for a provider that takes real time to answer."""
    time.sleep(STAGE_SECONDS)
    return StageReasoning(
        stage=stage,
        packages=[PackageSelection(
            fragnet_id='frag.substructure.raft', why='slow stub', confidence=0.9,
            effective_confidence=0.5, sources=['frag.substructure.raft'],
        )] if stage == 'substructure' else [],
        library_version='v1', corpus_version='v1', prompt_version='v1',
    )


@pytest.fixture()
def server(monkeypatch):
    """A real uvicorn on a real loop, with a deliberately slow reasoner."""
    import uvicorn

    import backend.app.main as main_module
    import backend.app.simulator.runner as runner_module
    from backend.app.simulator import Simulator, registry

    monkeypatch.setattr(runner_module, 'reason_stage', _slow_reasoner)
    # Two stages is enough to show the behaviour and keeps the test near six seconds.
    monkeypatch.setattr(
        main_module, 'build_simulator',
        lambda brief, run_id: Simulator(
            brief, run_id=run_id, adapter=None, stages=['design', 'substructure'],
        ),
    )
    registry.clear()

    port = _free_port()
    config = uvicorn.Config(
        main_module.app, host='127.0.0.1', port=port, log_level='error',
        ws_ping_interval=None, ws_ping_timeout=None,
    )
    uvicorn_server = uvicorn.Server(config)
    thread = threading.Thread(target=uvicorn_server.run, daemon=True)
    thread.start()

    deadline = time.time() + 30
    while time.time() < deadline and not uvicorn_server.started:
        time.sleep(0.05)
    assert uvicorn_server.started, 'uvicorn did not start'

    yield f'127.0.0.1:{port}'

    uvicorn_server.should_exit = True
    thread.join(timeout=30)
    assert not thread.is_alive(), 'uvicorn did not stop; a run may still be walking'
    registry.clear()


def _drain(ws, budget: float = STAGE_SECONDS * 6):
    """Read until the walk settles, so nothing is left running server-side."""
    import json

    seen = []
    deadline = time.time() + budget
    while time.time() < deadline:
        try:
            seen.append(json.loads(ws.recv(timeout=STAGE_SECONDS * 3)).get('type'))
        except Exception:
            break
        if seen[-1] in ('simulation_completed', 'simulation_halted', 'simulation_error'):
            break
    return seen


def _health(base: str, timeout: float = 20.0):
    started = time.perf_counter()
    with urllib.request.urlopen(f'http://{base}/health', timeout=timeout) as response:
        response.read()
    return time.perf_counter() - started


def test_health_answers_while_a_run_is_reasoning(server):
    """The assertion the outage was about: one run must not make the service unavailable."""
    from websockets.sync.client import connect

    idle = _health(server)
    assert idle < HEALTH_BUDGET, f'the service was already slow when idle ({idle:.2f}s)'

    with connect(f'ws://{server}/ws/simulate', open_timeout=20) as ws:
        ws.send('{"action": "start", "brief": {"project_name": "Loop", "city": "Chennai"}}')

        # The first stage is now sleeping for STAGE_SECONDS. Probe health throughout it: under
        # the old code every one of these waited for the stage to finish.
        worst = 0.0
        probes = 0
        deadline = time.time() + STAGE_SECONDS * 0.8
        while time.time() < deadline:
            worst = max(worst, _health(server))
            probes += 1
            time.sleep(0.2)

        assert probes >= 3, 'not enough probes landed inside the reasoning window'
        assert worst < HEALTH_BUDGET, (
            f'/health took {worst:.2f}s while a stage was reasoning ({STAGE_SECONDS}s). '
            'The event loop is blocked: one run is making the whole service unavailable.'
        )

        # And the run itself still works - the fix must not have broken streaming.
        seen = _drain(ws)
        assert 'simulation_started' in seen, f'the run never started: {seen}'
        assert seen[-1] != 'simulation_error', f'the run errored: {seen}'


def test_a_second_client_is_served_while_the_first_is_reasoning(server):
    """The multi-tenant version of the same fault: a second visitor must not be shut out."""
    from websockets.sync.client import connect

    with connect(f'ws://{server}/ws/simulate', open_timeout=20) as first:
        first.send('{"action": "start", "brief": {"project_name": "A", "city": "Chennai"}}')
        time.sleep(0.4)  # let the first stage get into its blocking call

        started = time.perf_counter()
        with connect(f'ws://{server}/ws/simulate', open_timeout=10) as second:
            # Attaching to a run that does not exist is a cheap round trip that still requires
            # the loop to be alive.
            second.send('{"action": "attach", "run_id": "run-does-not-exist"}')
            second.recv(timeout=10)
        elapsed = time.perf_counter() - started

        # Let the first run settle before dropping its socket. Walking away mid-stage leaves
        # the server finishing the walk on its own, and it then writes the run into the shared
        # registry AFTER this module's fixture has cleaned up - which surfaced as an unrelated
        # test in test_simulator.py finding a stray run. Tests that leak into other tests are
        # worse than the thing being tested.
        _drain(first)

    assert elapsed < HEALTH_BUDGET, (
        f'a second client waited {elapsed:.2f}s to be served while the first was reasoning'
    )
