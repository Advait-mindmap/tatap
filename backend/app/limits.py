"""Usage caps, so a public deployment cannot run away with the owner's LLM credits.

Every visitor's simulation spends real money: a full thirteen-stage walk is a model call per
stage plus one at intake, and nothing stopped a single tab — or a crawler — from starting them
back to back. Two caps, both configurable by environment variable:

* ``LLM_DAILY_CALL_CAP`` — a global ceiling on provider calls per UTC day. This is the one that
  actually protects the budget, because it counts the thing being spent rather than a proxy for
  it.
* ``RUNS_PER_CLIENT_DAILY`` — how many simulations one caller may start per UTC day. This stops
  one visitor monopolising the global budget. It is a courtesy limit, not a security boundary:
  the client key comes from ``X-Forwarded-For`` behind Railway's proxy, which a determined
  caller can spoof. The global cap is the backstop that does not depend on trusting a header.

Set either to ``0`` to disable it.

Counters live in Postgres (``UsageCounter``), not in memory. An in-process counter resets on
every deploy and restart, so on a platform that restarts freely it would enforce nothing.

**A counter that cannot be read fails closed.** If the database is unreachable the request is
refused rather than waved through — the entire purpose of this module is that spending cannot
happen unmetered, and a guard that silently stops guarding when infrastructure wobbles is the
failure it was written to prevent. Durable runs make the opposite trade (they fail open),
because there the cost of failure is inconvenience rather than an unbounded bill.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

#: Enough for a working day of genuine use — roughly a dozen full runs — and far below anything
#: that would empty an account overnight.
DEFAULT_LLM_DAILY_CALL_CAP = 400
DEFAULT_RUNS_PER_CLIENT_DAILY = 5

LLM_CALL_SCOPE = 'llm_calls'


class CapExceeded(RuntimeError):
    """A capped resource is spent for today."""

    def __init__(self, scope: str, limit: int, used: int, detail: str = '') -> None:
        self.scope = scope
        self.limit = limit
        self.used = used
        self.detail = detail or (
            f'Daily limit reached for {scope} ({used}/{limit}). It resets at 00:00 UTC.'
        )
        super().__init__(self.detail)


class CapUnavailable(RuntimeError):
    """The counter could not be read, so nothing may be spent. Fails closed by design."""


def _int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or '').strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def llm_daily_cap() -> int:
    return _int_env('LLM_DAILY_CALL_CAP', DEFAULT_LLM_DAILY_CALL_CAP)


def runs_per_client_cap() -> int:
    return _int_env('RUNS_PER_CLIENT_DAILY', DEFAULT_RUNS_PER_CLIENT_DAILY)


def today() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


def _session(session_factory: Any = None):
    if session_factory is not None:
        return session_factory()
    from backend.app.database import SessionLocal, ensure_tables

    ensure_tables()
    return SessionLocal()


def reserve(
    scope: str,
    limit: int,
    *,
    amount: int = 1,
    session_factory: Any = None,
    day: Optional[str] = None,
) -> int:
    """Charge `amount` against `scope` for today. Returns the new total.

    Raises `CapExceeded` if the charge would cross the limit, and `CapUnavailable` if the
    counter cannot be reached. `limit <= 0` means uncapped, and does not touch the database.
    """
    if limit <= 0:
        return 0

    from backend.app.models import UsageCounter

    stamp = day or today()
    try:
        session = _session(session_factory)
    except Exception as exc:  # noqa: BLE001 - fail closed, see module docstring
        raise CapUnavailable(f'usage counter unavailable: {exc}') from exc

    try:
        with session:
            row = (
                session.query(UsageCounter)
                .filter(UsageCounter.day == stamp, UsageCounter.scope == scope)
                .one_or_none()
            )
            if row is None:
                row = UsageCounter(day=stamp, scope=scope, count=0)
                session.add(row)
                session.flush()
            if row.count + amount > limit:
                raise CapExceeded(scope, limit, row.count)
            row.count += amount
            total = row.count
            session.commit()
            return total
    except CapExceeded:
        raise
    except Exception as exc:  # noqa: BLE001
        raise CapUnavailable(f'usage counter unavailable: {exc}') from exc


def used(scope: str, *, session_factory: Any = None, day: Optional[str] = None) -> int:
    """How much of `scope` has been spent today. Zero if it has never been charged."""
    from backend.app.models import UsageCounter

    stamp = day or today()
    try:
        session = _session(session_factory)
        with session:
            row = (
                session.query(UsageCounter)
                .filter(UsageCounter.day == stamp, UsageCounter.scope == scope)
                .one_or_none()
            )
            return row.count if row else 0
    except Exception:  # noqa: BLE001 - a reporting read, not a gate
        return 0


class GuardedAdapter:
    """An LLM adapter that charges the daily budget before every call.

    Wrapping the adapter rather than the endpoint is what makes the cap accurate: one intake is
    a single call, but one simulation is a call per stage, and any cap counted in requests would
    be out by a factor of thirteen.

    The charge happens BEFORE the call. Charging after would let a burst of concurrent requests
    all pass the check and spend together, and a provider error still consumes the attempt.
    """

    def __init__(
        self,
        inner: Any,
        *,
        limit: Optional[int] = None,
        scope: str = LLM_CALL_SCOPE,
        session_factory: Any = None,
    ) -> None:
        self.inner = inner
        self.limit = llm_daily_cap() if limit is None else limit
        self.scope = scope
        self._session_factory = session_factory

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        reserve(self.scope, self.limit, session_factory=self._session_factory)
        return self.inner.invoke(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        # Anything else the reasoning layer asks of an adapter passes straight through, so the
        # wrapper stays invisible to callers that use more than `invoke`.
        return getattr(self.inner, name)


def guarded_adapter(inner: Any = None, **kwargs: Any) -> GuardedAdapter:
    """The configured provider, wrapped in the daily budget."""
    if inner is None:
        from backend.app.llm import get_adapter

        inner = get_adapter()
    return GuardedAdapter(inner, **kwargs)


def client_key(headers: Any, fallback: str = 'unknown') -> str:
    """Identify the caller for the per-client run cap.

    Railway terminates TLS in front of the app, so the socket peer is always the proxy and
    `X-Forwarded-For` carries the real client. That header is caller-supplied and spoofable,
    which is why this limit is a courtesy and the global cap is the real protection.
    """
    try:
        forwarded = headers.get('x-forwarded-for') or ''
    except Exception:  # noqa: BLE001
        forwarded = ''
    if forwarded:
        return forwarded.split(',')[0].strip() or fallback
    return fallback or 'unknown'
