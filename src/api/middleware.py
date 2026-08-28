"""aiohttp middleware: API-wide client concurrency cap and per-route
request timeouts. Kept out of `main.py`/`app.py` per AGENTS.md.
"""

from collections.abc import Callable

from aiohttp.typedefs import Handler, Middleware

RouteTimeoutAttr = "smores_timeout_seconds"
"""Attribute name `with_timeout` sets on a decorated handler function;
read back by the timeout middleware to find a per-route override."""


def with_timeout(seconds: float) -> Callable[[Handler], Handler]:
    """Decorator marking a route handler's timeout override (e.g.
    `/api/sensors/current`'s `config.poll_timeout_seconds`), read by
    `create_timeout_middleware`. Undecorated handlers use that
    middleware's `default_timeout_seconds`.
    """
    raise NotImplementedError


def create_timeout_middleware(default_timeout_seconds: float) -> Middleware:
    """Build a `@web.middleware` that runs each handler under
    `asyncio.wait_for`, using the handler's `with_timeout`-set override if
    present, else `default_timeout_seconds`.

    On expiry, responds `504 Gateway Timeout` with a JSON error body
    instead of propagating `TimeoutError`.
    """
    raise NotImplementedError


def create_concurrency_limit_middleware(max_concurrent_clients: int) -> Middleware:
    """Build a `@web.middleware` enforcing at most `max_concurrent_clients`
    requests in flight at once via an `asyncio.Semaphore`.

    A request arriving with no capacity available gets an immediate
    `503 Service Unavailable` with a `Retry-After` header — never queued —
    per AGENTS.md.
    """
    raise NotImplementedError


__all__ = [
    "create_concurrency_limit_middleware",
    "create_timeout_middleware",
    "with_timeout",
]
