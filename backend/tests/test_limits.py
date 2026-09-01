"""The deployment must not be able to spend an unbounded amount of the owner's LLM credits.

A public URL where every visitor triggers fourteen model calls is an open tap. These tests pin
the two taps shut: a global daily ceiling on provider calls, and a per-caller ceiling on runs.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app import limits
from backend.app.limits import (
    CapExceeded,
    CapUnavailable,
    GuardedAdapter,
    client_key,
    reserve,
    used,
)
from backend.app.models import Base


@pytest.fixture()
def factory(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "usage.db"}', future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class CountingAdapter:
    """Stands in for a provider, and records what it was actually asked to do."""

    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, **kwargs):
        self.calls += 1
        return {'ok': True}


# --------------------------------------------------------------------------- the counter

def test_reserve_counts_up_and_then_refuses(factory):
    for expected in (1, 2, 3):
        assert reserve('llm_calls', 3, session_factory=factory) == expected

    with pytest.raises(CapExceeded) as raised:
        reserve('llm_calls', 3, session_factory=factory)
    assert raised.value.limit == 3
    assert raised.value.used == 3
    assert 'resets at 00:00 UTC' in raised.value.detail


def test_the_count_is_per_day_so_the_cap_resets(factory):
    reserve('llm_calls', 1, session_factory=factory, day='2026-08-31')
    with pytest.raises(CapExceeded):
        reserve('llm_calls', 1, session_factory=factory, day='2026-08-31')
    # A new day starts a new budget rather than staying spent forever.
    assert reserve('llm_calls', 1, session_factory=factory, day='2026-09-01') == 1


def test_scopes_are_counted_separately(factory):
    reserve('runs:1.2.3.4', 2, session_factory=factory)
    reserve('runs:1.2.3.4', 2, session_factory=factory)
    with pytest.raises(CapExceeded):
        reserve('runs:1.2.3.4', 2, session_factory=factory)
    # One caller exhausting their allowance must not lock out everyone else.
    assert reserve('runs:5.6.7.8', 2, session_factory=factory) == 1


def test_a_zero_cap_means_unlimited_and_never_touches_the_database(factory):
    for _ in range(50):
        assert reserve('llm_calls', 0, session_factory=factory) == 0
    assert used('llm_calls', session_factory=factory) == 0


def test_the_count_survives_a_restart(factory):
    """The reason the counter is in the database at all.

    An in-process counter would reset on every deploy, so a cap of 400/day would really be
    400 calls per restart — on a platform that restarts freely, no cap at all.
    """
    for _ in range(4):
        reserve('llm_calls', 5, session_factory=factory)

    # Nothing in this process is reused; only the stored rows carry the count forward.
    assert used('llm_calls', session_factory=factory) == 4
    reserve('llm_calls', 5, session_factory=factory)
    with pytest.raises(CapExceeded):
        reserve('llm_calls', 5, session_factory=factory)


# --------------------------------------------------------------------------- fail closed

def test_an_unreachable_counter_refuses_rather_than_waving_the_call_through():
    """A guard that stops guarding when the database wobbles is the bug, not the safe default."""

    def broken():
        raise RuntimeError('database is down')

    with pytest.raises(CapUnavailable):
        reserve('llm_calls', 10, session_factory=broken)


# --------------------------------------------------------------------------- the wrapper

def test_the_guarded_adapter_charges_once_per_call_and_then_blocks(factory):
    inner = CountingAdapter()
    adapter = GuardedAdapter(inner, limit=2, session_factory=factory)

    adapter.invoke(system='s', user='u', schema={})
    adapter.invoke(system='s', user='u', schema={})
    assert inner.calls == 2

    with pytest.raises(CapExceeded):
        adapter.invoke(system='s', user='u', schema={})
    # The blocked call never reached the provider, so it cost nothing.
    assert inner.calls == 2


def test_the_guard_charges_per_model_call_not_per_request(factory):
    """One simulation is a call per stage. A cap counted in requests would be out by 13x."""
    inner = CountingAdapter()
    adapter = GuardedAdapter(inner, limit=100, session_factory=factory)
    for _ in range(13):
        adapter.invoke(system='s', user='u', schema={})
    assert used('llm_calls', session_factory=factory) == 13


def test_the_wrapper_is_transparent_to_everything_else(factory):
    inner = CountingAdapter()
    inner.model = 'claude_opus_4_8'
    adapter = GuardedAdapter(inner, limit=10, session_factory=factory)
    assert adapter.model == 'claude_opus_4_8'


# --------------------------------------------------------------------------- configuration

def test_caps_come_from_the_environment(monkeypatch):
    monkeypatch.setenv('LLM_DAILY_CALL_CAP', '17')
    monkeypatch.setenv('RUNS_PER_CLIENT_DAILY', '3')
    assert limits.llm_daily_cap() == 17
    assert limits.runs_per_client_cap() == 3

    # A malformed value falls back to the default instead of disabling the cap by accident —
    # LLM_DAILY_CALL_CAP="unlimited" must not mean unlimited.
    monkeypatch.setenv('LLM_DAILY_CALL_CAP', 'unlimited')
    assert limits.llm_daily_cap() == limits.DEFAULT_LLM_DAILY_CALL_CAP

    monkeypatch.delenv('LLM_DAILY_CALL_CAP')
    assert limits.llm_daily_cap() == limits.DEFAULT_LLM_DAILY_CALL_CAP


def test_defaults_are_capped_not_unlimited():
    """A deployment that sets nothing still has to be protected."""
    assert limits.DEFAULT_LLM_DAILY_CALL_CAP > 0
    assert limits.DEFAULT_RUNS_PER_CLIENT_DAILY > 0


# --------------------------------------------------------------------------- client key

def test_the_client_key_reads_the_forwarded_header_behind_the_proxy():
    assert client_key({'x-forwarded-for': '203.0.113.7, 10.0.0.1'}) == '203.0.113.7'


def test_the_client_key_falls_back_to_the_socket_peer():
    assert client_key({}, fallback='198.51.100.2') == '198.51.100.2'
    assert client_key({}) == 'unknown'


# --------------------------------------------------------------- the caps at the API edge

def test_intake_returns_429_when_the_daily_budget_is_spent(monkeypatch):
    """The endpoint has to surface the cap, not crash or silently spend past it."""
    from fastapi.testclient import TestClient

    from backend.app.main import app

    def spent(*args, **kwargs):
        raise CapExceeded('llm_calls', 400, 400)

    monkeypatch.setattr('backend.app.main.guarded_adapter', spent)
    response = TestClient(app).post('/intake', json={'text': 'A 20 MW Tier III DC in Chennai.'})

    assert response.status_code == 429
    assert 'Daily limit reached' in response.json()['detail']


def test_intake_returns_503_when_metering_is_down(monkeypatch):
    """Fails closed: no metering, no spending."""
    from fastapi.testclient import TestClient

    from backend.app.main import app

    def unavailable(*args, **kwargs):
        raise CapUnavailable('database is down')

    monkeypatch.setattr('backend.app.main.guarded_adapter', unavailable)
    response = TestClient(app).post('/intake', json={'text': 'A 20 MW Tier III DC in Chennai.'})

    assert response.status_code == 503
    assert 'metering is unavailable' in response.json()['detail']


def test_the_websocket_refuses_a_run_once_the_client_has_had_its_allowance(monkeypatch):
    """One visitor cannot start simulations without limit.

    The run never begins, so the refusal costs nothing: the point of a per-client cap is that
    the expensive part is not reached.
    """
    from fastapi.testclient import TestClient

    from backend.app.main import app

    started = []

    def never(*args, **kwargs):
        started.append(args)
        raise AssertionError('a capped run must not build a simulator')

    monkeypatch.setattr('backend.app.main.build_simulator', never)
    monkeypatch.setattr(
        'backend.app.main.reserve',
        lambda scope, limit, **kw: (_ for _ in ()).throw(CapExceeded(scope, 5, 5)),
    )

    with TestClient(app).websocket_connect('/ws/simulate') as ws:
        ws.send_json({'action': 'start', 'brief': {'city': 'Chennai'}})
        event = ws.receive_json()

    assert event['type'] == 'simulation_error'
    assert 'Daily limit reached' in event['payload']['error']
    assert not started
