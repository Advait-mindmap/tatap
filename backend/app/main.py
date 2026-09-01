import logging
import os
from typing import Any, Dict

from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.app.intake import extract_brief
from backend.app.limits import (
    CapExceeded,
    CapUnavailable,
    client_key,
    guarded_adapter,
    reserve,
    runs_per_client_cap,
)
from backend.app.llm import LLMError
from backend.app.schemas import IntakeResult, RawBrief
from backend.app.simulator import DecisionAnswer, Simulator, registry

app = FastAPI(title='DC Build Planner')


@app.on_event('startup')
def _create_tables() -> None:
    """Make sure the schema exists before the first request touches it.

    `init_db()` was exported from the app package but never called, so a fresh deployment ran
    against an empty database — invisible while every feature was in-memory, fatal now that runs
    and usage counters are stored. A failure here is logged rather than fatal: the API without
    durable runs is degraded, but the API refusing to boot is down.
    """
    try:
        from backend.app.database import ensure_tables

        ensure_tables()
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning('could not create tables at startup: %s', exc)


# The planner UI is served from a different origin in development (Vite on :5173) and may be
# served from a separate static host in deployment. Origins come from CORS_ORIGINS so a
# deployment can narrow them; the default covers local development only.
_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        'CORS_ORIGINS', 'http://localhost:5173,http://127.0.0.1:5173'
    ).split(',')
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=['*'],
    allow_headers=['*'],
)


def build_simulator(brief: Dict[str, Any], run_id: str) -> Simulator:
    """Factory, so tests can substitute a simulator without patching the endpoint.

    The adapter is wrapped in the daily budget here, at the edge, rather than inside
    `get_adapter()`: the library and test paths call the provider directly and should not be
    metered by a deployment's caps.
    """
    return Simulator(brief, run_id=run_id, adapter=guarded_adapter())


@app.get('/health')
def health():
    return {'status': 'ok', 'service': 'dc-build-planner'}


class IntakeRequest(BaseModel):
    """Free text pasted at intake, or the extracted text of uploaded documents."""

    text: str = Field(min_length=1)
    source_ref: str = 'raw_brief'
    attachments: list[str] = Field(default_factory=list)


@app.post('/intake', response_model=IntakeResult)
def intake(request: IntakeRequest) -> IntakeResult:
    """Read a raw brief into a structured Brief with a citation per field and questions.

    Returns 200 with questions[] populated when fields are missing — an incomplete brief is a
    normal outcome, not an error. The planner answers the questions and resubmits.
    """
    try:
        return extract_brief(
            RawBrief(
                text=request.text,
                source_ref=request.source_ref,
                attachments=request.attachments,
            ),
            adapter=guarded_adapter(),
        )
    except CapExceeded as exc:
        # 429, not 503: the service is healthy, the budget for today is not.
        raise HTTPException(status_code=429, detail=exc.detail) from exc
    except CapUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail=f'Usage metering is unavailable, so requests are refused: {exc}',
        ) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f'Extraction provider failed: {exc}') from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.websocket('/ws/simulate')
async def ws_simulate(websocket: WebSocket) -> None:
    """Stream a simulation, pausing at genuine forks until the client answers.

    Protocol, client -> server:
        {"action": "start",  "brief": {...}}          begin a run
        {"action": "answer", "decision_point_id": ..., "answer": ...}   resolve a fork
        {"action": "resume"}                          continue after answering
        {"action": "stop"}                            end the run

    Server -> client: one SimulationEvent per message (see simulator/events.py).

    The run lives in the registry rather than in this coroutine, so a dropped socket does not
    lose a halted run: the client reconnects with {"action": "attach", "run_id": ...} and
    answers the outstanding fork.
    """
    await websocket.accept()
    simulator: Simulator | None = None

    async def fail(error: str) -> None:
        await websocket.send_json({
            'seq': simulator.state.seq if simulator else 0,
            'type': 'simulation_error', 'stage': '', 'payload': {'error': error},
        })

    async def stream() -> None:
        """Run until the walk settles, then checkpoint.

        The checkpoint is after streaming rather than per event: a halt and a completion are the
        only points where the run is worth resuming from, and writing on every activity would
        put a database round trip inside the draw loop.
        """
        try:
            for event in simulator.run():
                await websocket.send_json(event.to_wire())
        except CapExceeded as exc:
            # The run keeps its state and stays stored, so it can be resumed tomorrow rather
            # than having to be re-reasoned from the brief.
            registry.save(simulator)
            await fail(exc.detail)
            return
        except CapUnavailable as exc:
            registry.save(simulator)
            await fail(f'Usage metering is unavailable, so the run was stopped: {exc}')
            return
        registry.save(simulator)

    try:
        while True:
            message = await websocket.receive_json()
            action = (message.get('action') or '').lower()

            if action == 'start':
                caller = client_key(
                    websocket.headers,
                    fallback=websocket.client.host if websocket.client else 'unknown',
                )
                try:
                    reserve(f'runs:{caller}', runs_per_client_cap())
                except CapExceeded as exc:
                    await fail(exc.detail)
                    continue
                except CapUnavailable as exc:
                    await fail(f'Usage metering is unavailable, so the run was refused: {exc}')
                    continue

                run_id = registry.new_id()
                simulator = build_simulator(message.get('brief') or {}, run_id)
                registry.add(simulator)
                await stream()

            elif action == 'attach':
                # `get` falls back to storage, so this is also the reconnect-after-restart path:
                # the run is rebuilt from its stored state rather than reported as missing.
                simulator = registry.get(
                    message.get('run_id') or '', adapter=guarded_adapter()
                )
                if simulator is None:
                    await websocket.send_json({
                        'seq': 0, 'type': 'simulation_error', 'stage': '',
                        'payload': {'error': f'No run {message.get("run_id")!r}'},
                    })
                    continue
                # Re-raise the open forks as decision_needed so a reconnected client can render
                # and answer them. Without this the client knew a run was halted but not what it
                # was halted ON, which made attach useless for the case it exists for.
                for decision_id, pending in sorted(simulator.state.pending_decisions.items()):
                    await websocket.send_json({
                        'seq': simulator.state.seq, 'type': 'decision_needed',
                        'stage': pending.get('stage', ''),
                        'payload': {
                            'id': decision_id,
                            'question': pending.get('question', ''),
                            'why_stuck': pending.get('why_stuck', ''),
                            'options': pending.get('options', []),
                            'impact': pending.get('impact', ''),
                            'blocking': pending.get('blocking', True),
                            'detection': pending.get('detection', ''),
                        },
                    })
                await websocket.send_json({
                    'seq': simulator.state.seq,
                    'type': 'simulation_halted' if simulator.is_halted else 'simulation_started',
                    'stage': simulator.state.halted_at or '',
                    'payload': {
                        'run_id': simulator.state.run_id,
                        'completed_stages': simulator.state.completed_stages,
                        'pending': sorted(simulator.state.pending_decisions),
                        # The authoritative output, so the reconnected view redraws the plan
                        # built so far instead of starting from an empty canvas.
                        'output': simulator.output().model_dump(),
                    },
                })

            elif action == 'answer':
                if simulator is None:
                    continue
                try:
                    simulator.answer(DecisionAnswer(
                        decision_point_id=message.get('decision_point_id') or '',
                        answer=message.get('answer') or '',
                        answered_by=message.get('answered_by') or 'planner',
                    ))
                except KeyError as exc:
                    await websocket.send_json({
                        'seq': simulator.state.seq, 'type': 'simulation_error', 'stage': '',
                        'payload': {'error': str(exc)},
                    })
                    continue

                # Resume only once EVERY open fork is answered. A stage can raise several at
                # once, and resuming after each one makes the simulator re-reason the stage and
                # halt again on the remainder - the planner answers the same stage repeatedly
                # and pays a model call for each. Answering the last one continues the walk.
                # `answer()` records into state.answers; pending_decisions is only cleared when
                # run() emits decision_resolved, so is_halted is still True here. Compare the
                # two sets instead.
                unanswered = set(simulator.state.pending_decisions) - set(simulator.state.answers)
                if unanswered:
                    # Checkpoint the answer even though the walk is not resuming yet, so a
                    # restart between two answers to the same stage does not lose the first.
                    registry.save(simulator)
                    await websocket.send_json({
                        'seq': simulator.state.seq, 'type': 'decision_recorded', 'stage': '',
                        'payload': {
                            'decision_point_id': message.get('decision_point_id'),
                            'pending': sorted(unanswered),
                        },
                    })
                    continue
                await stream()

            elif action == 'resume':
                if simulator is not None:
                    await stream()

            elif action == 'stop':
                if simulator is not None:
                    registry.drop(simulator.state.run_id)
                await websocket.close()
                return

    except WebSocketDisconnect:
        # The run stays in the registry so it can be attached to and answered later.
        return


# ---------------------------------------------------------------------------------------------
# Serve the built frontend.
#
# The Dockerfile already builds the UI and copies it here, but nothing served it - the image
# contained a frontend no one could reach. Mounting it LAST matters: a mount at "/" swallows
# every path, so it has to come after /health, /intake and /ws/simulate are registered.
#
# Serving both from one origin is also why the frontend defaults to a same-origin API base: there
# is no cross-origin call to configure, and CORS only matters for local development.
# ---------------------------------------------------------------------------------------------
STATIC_DIR = Path(__file__).resolve().parent / 'static'

if STATIC_DIR.is_dir():
    # html=True serves index.html for "/" so the app loads at the bare domain.
    app.mount('/', StaticFiles(directory=str(STATIC_DIR), html=True), name='ui')
else:
    @app.get('/')
    def ui_not_built() -> dict:
        """Running from source without a build. Say so rather than 404ing mysteriously."""
        return {
            'status': 'api-only',
            'detail': (
                'The frontend has not been built into this deployment. Run `npm run build` in '
                '/frontend, or use the Vite dev server for local development.'
            ),
            'api': ['/health', '/intake', '/ws/simulate'],
        }
