"""The spend guard for every simulator path that can reach a real model (spec 004, lane W3-3).

Load mode and the model comparison run on the stub model by default and never spend money. A
run that may reach a real model is opt-in twice: the caller passes ``real=True`` (``--real`` on
the command line) **and** sets a hard cap in ``CRIBA_REAL_SPEND_CAP_USD``. The cap defaults to
0, and a cap of 0 refuses every real run before it starts.

The cap covers everything the guard has seen: the spend already recorded in the ledger file
(``CRIBA_REAL_SPEND_LEDGER``, optional, shared by every run that names it) plus this run. Before
each unit of work (one persona, one Candidate) the caller asks :meth:`SpendGuard.allow` with
the cost it expects; the guard says no when that could cross the cap, so a run stops *before*
it overspends, not after.

How a unit of work is priced, best source first:

1. **OpenRouter's own charge.** A usage row that carries ``reconciled_cost_usd`` (the summary
   call's ``usage.cost``) is a bill, not an estimate, and counts as it stands. Hermes runs carry
   no cost (spike 1), so their rows are priced from tokens.
2. **Tokens, per model that served the call.** The Operator cost block lists ``by_model``: tokens
   split by the model that served them. Each model is priced at its own rates
   (:data:`GUARD_PRICES_PER_MTOK`), so a Haiku turn is not priced as the Sonnet summary that
   follows it. Per model, ``input`` counts only the uncached tokens (``input_tokens`` minus
   ``cache_read_tokens``: Hermes and OpenRouter report the cached part inside the input count),
   cache reads are priced at the cache-read rate, cache writes at the cache-write rate and
   output at the output rate. The backend's own estimate counts when it is higher.
3. **No split** (an older backend): all tokens at the dearest model that served the run.

The guard never prices a non-free model it has no rate for, unless that model's rows carry
OpenRouter's charge: it refuses the run instead. Override or extend the rates with
``CRIBA_REAL_PRICES_JSON`` (``{"model": {"input": .., "output": .., "cache_read": ..,
"cache_write": ..}}``, USD per million tokens).

Free models cost 0 whatever their tokens: the stub model (``stub-model``) and the summary's
no-model client in sim mode (``deterministic-summary``), by name prefix
(:data:`FREE_MODEL_PREFIXES`).

A ledger over-count is replaced by the real bill with :func:`reconcile_ledger`
(``python -m sim.compare spend --reconcile <usd> --reason <text>``).
"""

from __future__ import annotations

import json
import logging
import math
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CAP_ENV = "CRIBA_REAL_SPEND_CAP_USD"
LEDGER_ENV = "CRIBA_REAL_SPEND_LEDGER"
PRICES_ENV = "CRIBA_REAL_PRICES_JSON"

# USD per million tokens. Source: OpenRouter's public model listing (GET /api/v1/models, no key,
# no model call), read on 2026-10-10 (PRICES_CHECKED):
#   anthropic/claude-haiku-5.5   prompt 0.10, completion 0.50, cache read 0.01,
#                                cache write 0.125 (5 min) / 0.20 (1 h)
#   anthropic/claude-sonnet-5.5  prompt 2.00, completion 10.00, cache read 0.10,
#                                cache write 2.50 (5 min) / 4.00 (1 h)
# These are the list prices, except cache_write: the usage rows do not say which cache lifetime
# was used, so the guard takes the dearer 1-hour rate. Haiku has a tier from 100,000 prompt
# tokens in one request (five times these rates); a turn is held to 10,000 tokens
# (turn-size.md), so it is out of reach. The backend keeps the same table
# (criba.channel.run_client.PRICES_PER_MTOK; it must not import this package) and a test
# (test_spend.py) keeps the two equal. Re-read the listing before a run when this date is old.
PRICES_CHECKED = "2026-10-10"
GUARD_PRICES_PER_MTOK: dict[str, dict[str, float]] = {
    "anthropic/claude-haiku-5.5": {
        "input": 0.10,
        "output": 0.50,
        "cache_read": 0.01,
        "cache_write": 0.20,
    },
    "anthropic/claude-sonnet-5.5": {
        "input": 2.00,
        "output": 10.00,
        "cache_read": 0.10,
        "cache_write": 4.00,
    },
    # Jev through OpenRouter's decisions endpoint (ADR 0051): a bound, not a list price. A call
    # of 400 input and 70 output tokens was billed 0.000017 USD (about 0.04 per million tokens).
    # Its rows carry OpenRouter's own charge, which counts as it stands; this row only prices a
    # failed call, which has none.
    "typesafe/jev-1.13": {"input": 0.10, "output": 0.10, "cache_read": 0.10, "cache_write": 0.10},
}

TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
)
_PRICE_KEYS = ("input", "output", "cache_read", "cache_write")


class SpendRefused(RuntimeError):
    """A real-model run was refused: no cap, a cap too small, or a model the guard cannot price."""


FREE_MODEL_PREFIXES = ("stub", "deterministic")


def is_stub_model(model: str | None) -> bool:
    """True for the free sim models (the stub, the deterministic summary)."""
    return bool(model) and str(model).lower().startswith(FREE_MODEL_PREFIXES)


UNKNOWN_MODEL = "unknown"  # what the backend records when a run never reached a model


def is_refused_run(model: str | None, usage: Mapping[str, Any]) -> bool:
    """True for a usage entry that is a run Hermes refused or never served.

    Hermes answers ``429 Too many concurrent runs`` past its cap, and the backend records that
    run with model ``unknown`` and no tokens (`criba.channel.run_client.record_usage`). Nothing
    was spent and no real model served it, so the stub-mode guard must not read it as a real
    model: that turns saturation into an abort. An ``unknown`` entry with any token or any cost
    is not such a run and still counts. `usage` is a ``by_model`` entry, or the run's totals
    with ``est_cost_usd`` set to the backend's figure.
    """
    if str(model or "").lower() != UNKNOWN_MODEL:
        return False
    if any(_tokens(usage, key) for key in TOKEN_KEYS):
        return False
    return not any(
        float(usage.get(key) or 0.0) > 0 for key in ("est_cost_usd", "reconciled_cost_usd")
    )


def prices_from_env(
    env: Mapping[str, str] | None = None,
) -> dict[str, dict[str, float]]:
    env = os.environ if env is None else env
    prices = {k: dict(v) for k, v in GUARD_PRICES_PER_MTOK.items()}
    raw = env.get(PRICES_ENV)
    if raw:
        extra = json.loads(raw)
        if not isinstance(extra, dict):
            raise ValueError(f"{PRICES_ENV} must be a JSON object")
        for model, row in extra.items():
            prices[model] = {k: float(row[k]) for k in _PRICE_KEYS}
    return prices


def cap_from_env(env: Mapping[str, str] | None = None) -> float:
    env = os.environ if env is None else env
    raw = (env.get(CAP_ENV) or "0").strip()
    try:
        cap = float(raw)
    except ValueError as exc:
        raise SpendRefused(f"{CAP_ENV}={raw!r} is not a number") from exc
    if cap < 0:
        raise SpendRefused(f"{CAP_ENV} must not be negative")
    return cap


def _tokens(tokens: Mapping[str, Any], key: str) -> int:
    return max(0, int(tokens.get(key) or 0))


def call_cost(
    model: str,
    tokens: Mapping[str, Any],
    prices: Mapping[str, Mapping[str, float]],
) -> float:
    """Cost in USD of `tokens` served by one `model`, each kind at that model's own rate.

    Input is the *uncached* part only: ``input_tokens`` includes the cache reads (Hermes and
    OpenRouter report it so), which are priced once, at the cache-read rate. When a source
    reports ``input_tokens`` below ``cache_read_tokens`` it cannot have included them, so the
    input is taken as it stands: the guard never subtracts its way to an under-count. Cache
    writes are left in the uncached input as well (an upper bound, at most one input rate more
    per written token). Stub models are free; a model missing from `prices` raises SpendRefused.
    """
    if is_stub_model(model):
        return 0.0
    if model not in prices:
        raise SpendRefused(f"no guard price for {model}; add it to {PRICES_ENV}")
    row = prices[model]
    inp = _tokens(tokens, "input_tokens")
    read = _tokens(tokens, "cache_read_tokens")
    uncached = inp - read if inp >= read else inp
    total = (
        uncached * row["input"]
        + read * row["cache_read"]
        + _tokens(tokens, "cache_write_tokens") * row["cache_write"]
        + _tokens(tokens, "output_tokens") * row["output"]
    )
    return round(total / 1_000_000, 8)


def token_cost(
    models: Iterable[str],
    tokens: Mapping[str, Any],
    prices: Mapping[str, Mapping[str, float]],
) -> float:
    """Cost in USD of `tokens` that are not split by model: the dearest of `models` (a run's
    served models). For a projection, and for a backend that does not report `by_model`.

    Stub models are free. A non-stub model missing from `prices` raises SpendRefused: the guard
    does not guess.
    """
    real = [m for m in models if not is_stub_model(m) and not is_refused_run(m, tokens)]
    if not real:
        return 0.0
    unknown = [m for m in real if m not in prices]
    if unknown:
        raise SpendRefused(
            f"no guard price for {', '.join(sorted(unknown))}; add it to {PRICES_ENV}"
        )
    return max(call_cost(m, tokens, prices) for m in real)


def by_model_cost(
    by_model: Sequence[Mapping[str, Any]],
    prices: Mapping[str, Mapping[str, float]],
) -> float:
    """Cost in USD of a cost block's ``by_model`` entries, each priced on its own.

    An entry with ``reconciled_cost_usd`` is OpenRouter's own charge and counts as it stands
    (no rate needed). Any other entry is priced from its tokens at its model's rates, or at the
    backend's estimate (``est_cost_usd``) when that is higher.
    """
    total = 0.0
    for entry in by_model:
        model = str(entry.get("model") or "")
        if is_stub_model(model) or is_refused_run(model, entry):
            continue
        billed = entry.get("reconciled_cost_usd")
        if billed is not None:
            total += max(0.0, float(billed))
            continue
        total += max(call_cost(model, entry, prices), float(entry.get("est_cost_usd") or 0.0))
    return round(total, 8)


def reported_cost(
    by_model: Sequence[Mapping[str, Any]],
    prices: Mapping[str, Mapping[str, float]],
    backend_usd: float = 0.0,
) -> float:
    """The cost a comparison report shows for one run, in USD: the best source per model.

    Per ``by_model`` entry: OpenRouter's own charge (``reconciled_cost_usd``) when it carries
    one; else its tokens at the serving model's own rates; else (a model with no rate here) the
    backend's estimate for the entry. Stub models are free. Without ``by_model`` (an older
    backend), the run's total backend estimate `backend_usd`.

    Unlike :func:`by_model_cost` this is a report figure, not a cap check: it never takes the
    higher of two numbers and never refuses a model it cannot price.
    """
    if not by_model:
        return round(max(0.0, float(backend_usd or 0.0)), 8)
    total = 0.0
    for entry in by_model:
        model = str(entry.get("model") or "")
        if is_stub_model(model):
            continue
        billed = entry.get("reconciled_cost_usd")
        if billed is not None:
            total += max(0.0, float(billed))
        elif model in prices:
            total += call_cost(model, entry, prices)
        else:
            total += max(0.0, float(entry.get("est_cost_usd") or 0.0))
    return round(total, 8)


@dataclass
class SpendGuard:
    """Counts spend against a hard cap.

    `real=False` is the stub mode: the cap is 0 and any cost above 0 (or any model that is not a
    stub) is a refusal, raised by :meth:`record`.
    """

    cap_usd: float = 0.0
    real: bool = False
    prior_usd: float = 0.0
    spent_usd: float = 0.0
    prices: dict[str, dict[str, float]] = field(
        default_factory=lambda: {k: dict(v) for k, v in GUARD_PRICES_PER_MTOK.items()}
    )
    ledger: Path | None = None
    label: str = ""

    @classmethod
    def from_env(
        cls, *, real: bool, label: str = "", env: Mapping[str, str] | None = None
    ) -> SpendGuard:
        """The guard for a run. Refuses a real run without a positive cap."""
        env = os.environ if env is None else env
        cap = cap_from_env(env) if real else 0.0
        if real and cap <= 0:
            raise SpendRefused(
                f"a real-model run needs a spend cap: set {CAP_ENV} to a positive number of USD "
                "(the default 0 refuses)"
            )
        ledger_path = env.get(LEDGER_ENV)
        ledger = Path(ledger_path) if ledger_path else None
        prior = read_ledger(ledger) if (ledger and real) else 0.0
        guard = cls(
            cap_usd=cap,
            real=real,
            prior_usd=prior,
            prices=prices_from_env(env),
            ledger=ledger,
            label=label,
        )
        if real and prior >= cap:
            raise SpendRefused(
                f"the ledger {ledger} already records ${prior:.4f}, at or over the ${cap:.2f} cap"
            )
        return guard

    @property
    def total_usd(self) -> float:
        return self.prior_usd + self.spent_usd

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.cap_usd - self.total_usd)

    def allow(self, projected_usd: float) -> bool:
        """True when a unit of work expected to cost `projected_usd` fits under the cap."""
        if not self.real:
            return True
        return self.total_usd + max(0.0, projected_usd) <= self.cap_usd

    def cost(
        self,
        models: Iterable[str],
        tokens: Mapping[str, Any],
        backend_usd: float = 0.0,
        by_model: Sequence[Mapping[str, Any]] | None = None,
    ) -> float:
        """The guard's cost for one unit of work.

        With `by_model` (tokens split by serving model, OpenRouter's own charge where a row
        carries it): each entry priced on its own, see :func:`by_model_cost`. Without it: the
        run's tokens at the dearest model it served, or the backend's estimate when higher.
        """
        if by_model:
            return by_model_cost(by_model, self.prices)
        return max(token_cost(models, tokens, self.prices), float(backend_usd or 0.0))

    def record(
        self,
        models: Iterable[str],
        tokens: Mapping[str, Any],
        backend_usd: float = 0.0,
        by_model: Sequence[Mapping[str, Any]] | None = None,
    ) -> float:
        """Add one unit's spend. In stub mode a real model or any cost raises SpendRefused.

        Runs Hermes refused (see :func:`is_refused_run`) are not a real model: they are skipped
        here, and the load report counts them as errors.
        """
        models = list(models)
        if not self.real:
            if by_model:
                served = [
                    str(e.get("model") or "")
                    for e in by_model
                    if not is_refused_run(e.get("model"), e)
                ]
            else:
                served = [
                    m
                    for m in models
                    if not is_refused_run(m, {**tokens, "est_cost_usd": backend_usd})
                ]
            real_models = [m for m in served if not is_stub_model(m)]
            if real_models or float(backend_usd or 0.0) > 0:
                raise SpendRefused(
                    f"the stack served {', '.join(real_models) or 'a priced model'}, not the stub; "
                    f"a real-model run needs --real and {CAP_ENV}"
                )
            return 0.0
        cost = self.cost(models, tokens, backend_usd, by_model)
        self.spent_usd += cost
        return cost

    def summary(self) -> str:
        """The line every command prints last: what real models cost, and against which cap."""
        if not self.real:
            return "real-model spend: $0.0000 (stub model, no real model was called)"
        return (
            f"real-model spend: this run ${self.spent_usd:.4f}, earlier runs "
            f"${self.prior_usd:.4f}, total ${self.total_usd:.4f} of the ${self.cap_usd:.2f} cap "
            f"(guard prices; the OpenRouter bill is the figure that counts)"
        )

    def save(self) -> None:
        """Append this run's spend to the ledger file, when there is one and it spent anything."""
        if self.ledger is None or not self.real or self.spent_usd <= 0:
            return
        _append_entry(
            self.ledger,
            {
                "at": _now(),
                "label": self.label,
                "spent_usd": round(self.spent_usd, 6),
            },
        )


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ledger_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    body = json.loads(path.read_text(encoding="utf-8") or "{}")
    runs = body.get("runs", []) if isinstance(body, dict) else []
    return [r for r in runs if isinstance(r, dict)]


def _append_entry(path: Path, entry: dict[str, Any]) -> None:
    entries = _ledger_entries(path)
    entries.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"runs": entries}, indent=2) + "\n", encoding="utf-8")


RECONCILE_KIND = "reconcile"


def read_ledger(path: Path | None) -> float:
    """Total USD recorded in a spend ledger file (0 when there is none).

    Only a reconciliation entry may be negative (it replaces an over-count with the real bill);
    any other negative figure counts as 0, and the total is never below 0.
    """
    if path is None:
        return 0.0
    total = 0.0
    for r in _ledger_entries(path):
        amount = float(r.get("spent_usd") or 0.0)
        total += amount if r.get("kind") == RECONCILE_KIND else max(0.0, amount)
    return max(0.0, round(total, 6))


def reconcile_ledger(path: Path, billed_usd: float, reason: str) -> dict[str, Any]:
    """Replace the ledger's total with the real billed figure, by appending a correction entry.

    The entry holds the adjustment (billed minus the total so far; negative when the guard
    over-counted), the reason, the old total and the billed figure, and is logged. The ledger's
    total is `billed_usd` afterwards, so it can never be negative overall: a negative or
    non-finite figure, or an empty reason, is refused and nothing is written.
    """
    if not math.isfinite(billed_usd) or billed_usd < 0:
        raise SpendRefused(
            f"the billed figure must be a number of USD, 0 or more (got {billed_usd})"
        )
    reason = reason.strip()
    if not reason:
        raise SpendRefused("a reconciliation needs a reason (--reason)")
    before = read_ledger(path)
    entry = {
        "at": _now(),
        "label": f"{RECONCILE_KIND}: {reason}",
        "kind": RECONCILE_KIND,
        "spent_usd": round(billed_usd - before, 6),
        "reason": reason,
        "total_before_usd": before,
        "billed_usd": round(billed_usd, 6),
    }
    _append_entry(path, entry)
    log.warning(
        "spend ledger %s reconciled: was $%.4f, now $%.4f (adjustment %+.4f): %s",
        path,
        before,
        billed_usd,
        entry["spent_usd"],
        reason,
    )
    return entry


__all__ = [
    "CAP_ENV",
    "FREE_MODEL_PREFIXES",
    "GUARD_PRICES_PER_MTOK",
    "LEDGER_ENV",
    "PRICES_CHECKED",
    "PRICES_ENV",
    "RECONCILE_KIND",
    "UNKNOWN_MODEL",
    "SpendGuard",
    "SpendRefused",
    "by_model_cost",
    "call_cost",
    "is_refused_run",
    "is_stub_model",
    "prices_from_env",
    "read_ledger",
    "reconcile_ledger",
    "reported_cost",
    "token_cost",
]
