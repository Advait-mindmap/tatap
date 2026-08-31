from typing import Any, Dict

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from backend.app.intake import extract_brief
from backend.app.llm import LLMError
from backend.app.schemas import IntakeResult, RawBrief
from backend.app.simulator import DecisionAnswer, Simulator, registry

app = FastAPI(title='DC Build Planner')


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
                # Resume immediately: the fork is answered, so the walk can continue.
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
