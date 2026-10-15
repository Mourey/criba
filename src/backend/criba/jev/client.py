"""The Jev port and its OpenRouter client (ADR 0051).

Jev is TypeSafe's System One model: it answers typed questions about a state with calibrated
probabilities. Criba reaches it through OpenRouter's decisions endpoint, with the OpenRouter
key (``POST /api/alpha/decisions``, body ``{model, state, questions}``), not through TypeSafe's
own API. :class:`JevPort` is what the router depends on; tests use a fake, the sim stack uses a
stub server that speaks the same HTTP shape.

Privacy: the state holds Candidate text, so a request or response body is never logged. The
log line carries the response's model, id and usage only.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx

from criba.jev.battery import QUESTIONS, JevInput, Reading, build_state, parse_answers
from criba.jev.config import DEFAULT_MODEL, DEFAULT_TIMEOUT_S, OPENROUTER_DECISIONS_URL

# Calls in flight at once. A call given up on keeps its worker until httpx's own per-phase
# timeouts end it; with every worker busy a new call waits in the queue and, past its budget,
# fails as a timeout like any slow call.
_WORKERS = 4


@dataclass(frozen=True)
class JevResult:
    """One successful call: the reading, and what the ledger records about it."""

    reading: Reading
    model: str
    request_id: str | None = None
    provider: str = "typesafe"
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None  # OpenRouter's own charge for the call, when it reports one
    latency_ms: int = 0


class JevFailed(Exception):
    """The call failed: unreachable, too slow, a non-200 answer, or an answer we cannot read.

    ``detail`` is a code or an HTTP status, never a response body (it may echo Candidate text).
    """

    def __init__(
        self,
        detail: str,
        *,
        kind: Literal["error", "timeout"] = "error",
        latency_ms: int = 0,
    ) -> None:
        super().__init__(detail)
        self.kind = kind
        self.latency_ms = latency_ms


class JevPort(Protocol):
    model: str

    def read(self, request: JevInput) -> JevResult:
        """Read one Candidate message. Raises :class:`JevFailed`."""
        ...


@dataclass
class OpenRouterJev:
    """Jev through OpenRouter's decisions endpoint. One persistent HTTP client (keep-alive).

    ``timeout_s`` is the budget for the whole call. httpx applies a timeout to each phase
    (connect, write, read, pool) on its own, so a call slow in two phases could take twice as
    long; the request therefore runs on a small worker pool and the caller waits at most
    ``timeout_s`` for it. A call given up on finishes (or times out by phase) in the background
    and its answer is dropped.
    """

    api_key: str
    model: str = DEFAULT_MODEL
    url: str = OPENROUTER_DECISIONS_URL
    timeout_s: float = DEFAULT_TIMEOUT_S
    http: httpx.Client | None = None
    _client: httpx.Client = field(init=False, repr=False)
    _pool: ThreadPoolExecutor = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._client = self.http or httpx.Client(timeout=httpx.Timeout(self.timeout_s))
        self._pool = ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="jev")

    def read(self, request: JevInput) -> JevResult:
        started = time.monotonic()

        def elapsed_ms() -> int:
            return max(0, int((time.monotonic() - started) * 1000))

        body = {"model": self.model, "state": build_state(request), "questions": QUESTIONS}
        try:
            response = self._pool.submit(self._post, body).result(timeout=self.timeout_s)
        except FutureTimeout as exc:
            raise JevFailed("timeout", kind="timeout", latency_ms=elapsed_ms()) from exc
        except httpx.TimeoutException as exc:
            raise JevFailed("timeout", kind="timeout", latency_ms=elapsed_ms()) from exc
        except httpx.HTTPError as exc:
            raise JevFailed(f"unreachable: {type(exc).__name__}", latency_ms=elapsed_ms()) from exc
        if response.status_code != 200:
            raise JevFailed(f"http {response.status_code}", latency_ms=elapsed_ms())
        try:
            payload = response.json()
            reading = parse_answers(payload["answers"])
        except (ValueError, KeyError, TypeError) as exc:
            raise JevFailed("unreadable answer", latency_ms=elapsed_ms()) from exc
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        return JevResult(
            reading=reading,
            model=_text(payload.get("model")) or self.model,
            request_id=_text(payload.get("id")),
            provider=(_text(payload.get("provider")) or "typesafe").lower(),
            input_tokens=_count(usage.get("input_tokens")),
            output_tokens=_count(usage.get("output_tokens")),
            cost_usd=_cost(usage.get("cost")),
            latency_ms=elapsed_ms(),
        )

    def _post(self, body: dict[str, Any]) -> httpx.Response:
        """The request itself; each phase is also limited to the whole budget."""
        return self._client.post(
            self.url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=body,
            timeout=httpx.Timeout(self.timeout_s),
        )


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _cost(value: Any) -> float | None:
    """OpenRouter's ``usage.cost`` as a non-negative number, or None when absent or malformed."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if value >= 0 else None
