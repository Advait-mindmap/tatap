import os
from typing import Any, Dict

from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.app.intake import extract_brief
from backend.app.llm import LLMError
from backend.app.schemas import IntakeResult, RawBrief
from backend.app.simulator import DecisionAnswer, Simulator, registry

app = FastAPI(title='DC Build Planner')

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
    """Factory, so tests can substitute a simulator without patching the endpoint."""
    return Simulator(brief, run_id=run_id)


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
            )
        )
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

    async def stream() -> None:
        for event in simulator.run():
            await websocket.send_json(event.to_wire())

    try:
        while True:
            message = await websocket.receive_json()
            action = (message.get('action') or '').lower()

            if action == 'start':
                run_id = registry.new_id()
                simulator = build_simulator(message.get('brief') or {}, run_id)
                registry.add(simulator)
                await stream()

            elif action == 'attach':
                simulator = registry.get(message.get('run_id') or '')
                if simulator is None:
                    await websocket.send_json({
                        'seq': 0, 'type': 'simulation_error', 'stage': '',
                        'payload': {'error': f'No run {message.get("run_id")!r}'},
                    })
                    continue
                await websocket.send_json({
                    'seq': simulator.state.seq, 'type': 'simulation_halted'
                    if simulator.is_halted else 'simulation_started', 'stage':
                    simulator.state.halted_at or '',
                    'payload': {
                        'run_id': simulator.state.run_id,
                        'completed_stages': simulator.state.completed_stages,
                        'pending': sorted(simulator.state.pending_decisions),
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
