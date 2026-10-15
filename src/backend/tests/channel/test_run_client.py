"""Run client against a fake Hermes Runs API (spike 1 semantics), and the usage ledger."""

from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any

import httpx
import pytest

from criba.channel.config import ChannelSettings, usage_environment
from criba.channel.run_client import (
    STOP_TIMEOUT_S,
    USAGE_UNKNOWN,
    RunClient,
    RunFailed,
    estimate_cost,
    record_usage,
)
from criba.db import repo

from . import conftest as fake
from .conftest import API_KEY, BASE_URL, NOW, PROFILE, FakeHermes, make_run_client


def test_run_posts_to_profile_route_and_polls_to_completion(hermes: FakeHermes) -> None:
    hermes.running_polls = 3
    seen: list[httpx.Request] = []

    def spy(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return hermes.handler(request)

    client = RunClient(
        base_url=BASE_URL,
        profile=PROFILE,
        api_key=API_KEY,
        poll_interval_s=0,
        http=httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(spy)),
    )
    result = client.run(session_id="app_1", input="hola", idempotency_key="m-1")

    assert result.status == "completed" and result.output == "Recibido (IA): hola"
    assert result.usage["cache_read_tokens"] == 7000
    assert result.runtime["model"] == "anthropic/claude-haiku-5.5"
    post = seen[0]
    assert (post.method, post.url.path) == ("POST", "/p/criba-recruiter/v1/runs")
    assert post.headers["Authorization"] == f"Bearer {API_KEY}"
    assert post.headers["Idempotency-Key"] == "m-1"
    assert [r.url.path for r in seen[1:]] == ["/p/criba-recruiter/v1/runs/run_1"] * 4


def test_same_idempotency_key_replays_the_same_run(hermes: FakeHermes) -> None:
    client = make_run_client(hermes)
    first = client.run(session_id="app_1", input="hola", idempotency_key="k")
    second = client.run(session_id="app_1", input="hola", idempotency_key="k")
    assert first.run_id == second.run_id
    assert len(hermes.runs) == 1


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        (lambda h: setattr(h, "key", "other"), "HTTP 401"),
        (lambda h: setattr(h, "profile", "criba-recruiter-sim"), "HTTP 404"),
        (lambda h: setattr(h, "down", True), "unreachable"),
        (lambda h: setattr(h, "final_status", "failed"), "run failed"),
    ],
)
def test_errors_raise_run_failed(hermes: FakeHermes, setup, message: str) -> None:
    setup(hermes)
    with pytest.raises(RunFailed) as info:
        make_run_client(hermes).run(session_id="a", input="x", idempotency_key="k")
    assert info.value.kind == "error"
    assert message in str(info.value)


def test_timeout_raises_with_run_id(hermes: FakeHermes) -> None:
    hermes.running_polls = 10_000
    ticks = iter(range(0, 1000))
    client = make_run_client(
        hermes, timeout_s=5.0, monotonic=lambda: float(next(ticks)), sleep=lambda s: None
    )
    with pytest.raises(RunFailed) as info:
        client.run(session_id="a", input="x", idempotency_key="k")
    assert info.value.kind == "timeout"
    assert info.value.run_id == "run_1"
    assert info.value.latency_ms >= 5000


def test_output_as_text_parts(hermes: FakeHermes) -> None:
    hermes.reply = lambda text: [{"type": "text", "text": "Hola "}, {"text": "Ana"}]  # type: ignore[assignment,return-value]
    result = make_run_client(hermes).run(session_id="a", input="x", idempotency_key="k")
    assert result.output == "Hola Ana"


HAIKU = "anthropic/claude-haiku-5.5"
SONNET = "anthropic/claude-sonnet-5.5"


def test_estimate_cost() -> None:
    # 1M uncached input, 1M cache reads, 1M cache writes and 1M output. `input_tokens` is the
    # total prompt, cache reads included: 2M here, so 1M of it is uncached.
    usage = {
        "input_tokens": 2_000_000,
        "output_tokens": 1_000_000,
        "cache_read_tokens": 1_000_000,
        "cache_write_tokens": 1_000_000,
    }
    # Haiku: 0.10 input + 0.01 read + 0.20 write + 0.50 output.
    assert estimate_cost("anthropic/claude-haiku-5.5", usage) == pytest.approx(0.81)
    # Sonnet: 2.00 + 0.10 + 4.00 + 10.00.
    assert estimate_cost(SONNET, usage) == pytest.approx(16.10)
    assert estimate_cost("stub-model", usage) == 0.0


def test_cache_reads_are_not_charged_again_as_input() -> None:
    usage = {"input_tokens": 1_000_000, "cache_read_tokens": 1_000_000}
    assert estimate_cost(HAIKU, usage) == pytest.approx(0.01)
    # A source that reports fewer input tokens than cache reads did not include them.
    assert estimate_cost(HAIKU, {"input_tokens": 100, "cache_read_tokens": 1_000_000}) == (
        pytest.approx((100 * 0.10 + 1_000_000 * 0.01) / 1e6)
    )


def test_free_models_cost_nothing_and_an_unpriced_model_is_not_free() -> None:
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    assert estimate_cost("stub-model", usage) == 0.0
    assert estimate_cost("deterministic-summary", usage) == 0.0
    # No price row: the dearest known row, so the estimate cannot fall under the bill.
    assert estimate_cost("some/new-model", usage) == pytest.approx(12.0)
    assert estimate_cost(HAIKU, {}) == 0.0


def _list_price_bill(prices: dict[str, float], per_call: dict[str, int], calls: int) -> float:
    """What OpenRouter bills at list prices, cache writes at the 5-minute rate."""
    uncached = per_call["input"] - per_call["read"]
    one = (
        uncached * prices["input"]
        + per_call["read"] * prices["cache_read"]
        + per_call["write"] * prices["cache_write_5m"]
        + per_call["output"] * prices["output"]
    )
    return one * calls / 1e6


def _estimate_for(model: str, per_call: dict[str, int], calls: int) -> float:
    usage = {
        "input_tokens": per_call["input"] * calls,
        "cache_read_tokens": per_call["read"] * calls,
        "cache_write_tokens": per_call["write"] * calls,
        "output_tokens": per_call["output"] * calls,
    }
    return estimate_cost(model, usage)


def test_haiku_estimate_of_the_real_run_is_within_one_and_a_half_times_the_bill() -> None:
    # The real run of 2026-10-10 (model-comparison.md): 248 Haiku calls, median prompt 8,182
    # tokens, 98% read from the cache, billed $0.0356. The old estimate counted the cache reads
    # again as input and read $0.2504, 7 times the bill.
    per_call = {"input": 8_182, "read": 8_018, "write": 136, "output": 60}
    list_prices = {"input": 0.10, "cache_read": 0.01, "cache_write_5m": 0.125, "output": 0.50}
    billed = _list_price_bill(list_prices, per_call, 248)
    assert billed == pytest.approx(0.0356, rel=0.01)
    estimate = _estimate_for(HAIKU, per_call, 248)
    assert billed <= estimate <= 1.5 * billed
    old = 248 * (8_182 * 0.10 + 8_018 * 0.01 + 136 * 0.125 + 60 * 0.50) / 1e6
    assert old > 6 * billed


def test_sonnet_estimate_of_the_real_run_is_within_one_and_a_half_times_the_bill() -> None:
    # 204 Sonnet calls, median prompt 8,392 tokens, 98% cached, billed $0.4460. The backend
    # had no Sonnet row and estimated $0.
    per_call = {"input": 8_392, "read": 8_224, "write": 100, "output": 78}
    list_prices = {"input": 2.0, "cache_read": 0.10, "cache_write_5m": 2.5, "output": 10.0}
    billed = _list_price_bill(list_prices, per_call, 204)
    assert billed == pytest.approx(0.4460, rel=0.01)
    estimate = _estimate_for(SONNET, per_call, 204)
    assert billed <= estimate <= 1.5 * billed


def test_record_usage_stores_the_per_model_estimate(conn, hermes: FakeHermes) -> None:
    candidate, _ = repo.get_or_create_candidate(
        conn, client_id="grupo_sazon", channel="webchat", external_id="wc_e", country="ES"
    )
    app = repo.start_application(conn, candidate["id"], "webchat", now=NOW)
    result = make_run_client(hermes).run(session_id=app["id"], input="x", idempotency_key="k")
    result.runtime = {"model": SONNET, "provider": "openrouter"}
    result.usage = {"input_tokens": 10_000, "cache_read_tokens": 9_000, "output_tokens": 100}
    record_usage(conn, application=app, profile=PROFILE, environment="test", now=NOW, result=result)
    stored = conn.execute("SELECT est_cost_usd FROM usage").fetchone()[0]
    assert stored == pytest.approx((1_000 * 2.0 + 9_000 * 0.10 + 100 * 10.0) / 1e6)


def test_record_usage_counts_a_replayed_run_once(conn, hermes: FakeHermes) -> None:
    candidate, _ = repo.get_or_create_candidate(
        conn, client_id="grupo_sazon", channel="webchat", external_id="wc_x", country="ES"
    )
    app = repo.start_application(conn, candidate["id"], "webchat", now=NOW)
    result = make_run_client(hermes).run(session_id=app["id"], input="x", idempotency_key="k")
    for _ in range(2):
        record_usage(
            conn, application=app, profile=PROFILE, environment="test", now=NOW, result=result
        )
    assert conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0] == 1


def test_settings_from_env(tmp_path) -> None:
    key_file = tmp_path / "key"
    key_file.write_text("secret-key\n")
    settings = ChannelSettings.from_env(
        {
            "CRIBA_HERMES_PROFILE": "criba-recruiter-sim",
            "CRIBA_HERMES_API_KEY_FILE": str(key_file),
            "CRIBA_ENV": "sim",
            "CRIBA_RUN_TIMEOUT_S": "12",
        }
    )
    assert settings.hermes_profile == "criba-recruiter-sim"
    assert settings.environment == "sim" and settings.run_timeout_s == 12.0
    assert settings.read_api_key() == "secret-key"
    assert usage_environment(None) == "prod" and usage_environment("dev") == "prod"


# ---------------------------------------------------------------------------
# RunClient.stream: the run's events (spec R8.1; spike 5 row 6, spike 3 finding 1)
# ---------------------------------------------------------------------------


def _drain(gen: Generator[str, None, Any]) -> tuple[list[str], Any]:
    """Every delta the stream yields, and the RunResult it returns."""
    deltas: list[str] = []
    while True:
        try:
            deltas.append(next(gen))
        except StopIteration as stop:
            return deltas, stop.value


def _stream(client: RunClient, key: str = "m-1") -> Generator[str, None, Any]:
    return client.stream(session_id="app_1", input="hola", idempotency_key=key)


def _delta(seq: int | None, text: str, run_id: str | None = "run_1") -> str:
    data: dict[str, Any] = {"event": "message.delta", "delta": text}
    if run_id is not None:
        data["run_id"] = run_id
    return fake.sse(seq, data)


def _completed(seq: int, output: str, hermes: FakeHermes) -> str:
    return fake.sse(
        seq,
        {
            "event": "run.completed",
            "run_id": "run_1",
            "output": output,
            "usage": hermes.usage,
            "runtime": hermes.runtime,
        },
    )


def test_stream_yields_deltas_in_order_and_returns_the_completed_run(
    hermes: FakeHermes,
) -> None:
    client = make_run_client(hermes)
    deltas, result = _drain(_stream(client))

    assert len(deltas) == 3
    assert "".join(deltas) == "Recibido (IA): hola"
    assert result.run_id == "run_1" and result.status == "completed"
    assert result.output == "Recibido (IA): hola"
    assert result.usage == hermes.usage
    assert result.runtime == hermes.runtime
    assert hermes.posts == [
        {"body": {"input": "hola", "session_id": "app_1"}, "idempotency_key": "m-1"}
    ]
    assert hermes.polls == 0
    (events,) = hermes.event_requests
    assert events["accept"] == "text/event-stream"
    assert events["last_event_id"] is None


def test_stream_skips_comment_lines(hermes: FakeHermes) -> None:
    hermes.event_scripts = [
        [
            ": open\n\n",
            ": keepalive\n\n",
            _delta(0, "Ho"),
            ": keepalive\n\n",
            _delta(1, "la"),
            _completed(2, "Hola", hermes),
            ": stream closed\n\n",
        ]
    ]
    deltas, result = _drain(_stream(make_run_client(hermes)))
    assert deltas == ["Ho", "la"]
    assert result.output == "Hola"


def test_stream_ignores_frames_of_another_run_and_frames_without_run_id(
    hermes: FakeHermes,
) -> None:
    hermes.event_scripts = [
        [
            _delta(0, "Ho"),
            _delta(1, "OTHER", run_id="run_99"),
            _delta(2, "NONE", run_id=None),
            fake.sse(3, {"event": "run.completed", "run_id": "run_99", "output": "x"}),
            fake.sse(4, {"event": "run.failed", "error": "not ours"}),
            _delta(5, "la"),
            _completed(6, "Hola", hermes),
        ]
    ]
    deltas, result = _drain(_stream(make_run_client(hermes)))
    assert deltas == ["Ho", "la"]
    assert result.status == "completed" and result.output == "Hola"


def test_stream_ignores_other_events(hermes: FakeHermes) -> None:
    hermes.event_scripts = [
        [
            fake.sse(0, {"event": "tool.started", "run_id": "run_1", "tool": "get_application"}),
            fake.sse(1, {"event": "tool.completed", "run_id": "run_1", "error": False}),
            _delta(2, "Hola"),
            fake.sse(3, {"event": "reasoning.available", "run_id": "run_1", "text": "Hola"}),
            fake.sse(None, {"event": "replay.truncated", "run_id": "run_1"}),
            _completed(4, "Hola", hermes),
        ]
    ]
    deltas, result = _drain(_stream(make_run_client(hermes)))
    assert deltas == ["Hola"]
    assert result.output == "Hola"


def test_stream_drops_a_replayed_frame_already_handled(hermes: FakeHermes) -> None:
    hermes.event_scripts = [
        [
            _delta(0, "Ho"),
            _delta(1, "la"),
            _delta(1, "la"),
            _delta(0, "Ho"),
            _completed(2, "Hola", hermes),
        ]
    ]
    deltas, _ = _drain(_stream(make_run_client(hermes)))
    assert deltas == ["Ho", "la"]


def test_stream_resumes_after_a_drop_with_last_event_id_and_one_post(
    hermes: FakeHermes,
) -> None:
    hermes.event_scripts = [
        [": open\n\n", _delta(0, "Ho"), _delta(1, "la"), fake.drop()],
        # Hermes replays from the last id it was given; a repeated frame must not repeat a delta.
        [": open\n\n", _delta(1, "la"), _delta(2, " Ana"), _completed(3, "Hola Ana", hermes)],
    ]
    deltas, result = _drain(_stream(make_run_client(hermes)))

    assert deltas == ["Ho", "la", " Ana"]
    assert result.status == "completed" and result.output == "Hola Ana"
    assert len(hermes.posts) == 1
    assert [r["last_event_id"] for r in hermes.event_requests] == [None, "1"]
    assert all(r["accept"] == "text/event-stream" for r in hermes.event_requests)


def test_stream_read_timeout_counts_as_a_drop(hermes: FakeHermes) -> None:
    hermes.event_scripts = [[_delta(0, "Ho"), httpx.ReadTimeout("read timed out")]]
    deltas, result = _drain(_stream(make_run_client(hermes)))
    assert deltas[0] == "Ho"
    assert result.status == "completed"
    assert len(hermes.posts) == 1
    assert hermes.event_requests[1]["last_event_id"] == "0"


def test_stream_raises_after_the_fourth_drop(hermes: FakeHermes) -> None:
    hermes.event_scripts = [[_delta(n, f"d{n}"), fake.drop()] for n in range(4)]
    seen: list[str] = []
    with pytest.raises(RunFailed) as info:
        for delta in _stream(make_run_client(hermes)):
            seen.append(delta)
    assert seen == ["d0", "d1", "d2", "d3"]
    assert info.value.kind == "error"
    assert info.value.run_id == "run_1"
    assert len(hermes.event_requests) == 4
    assert len(hermes.posts) == 1


def test_stream_failure_event_raises_with_its_usage(hermes: FakeHermes) -> None:
    usage = {"input_tokens": 50, "output_tokens": 0, "total_tokens": 50}
    hermes.event_scripts = [
        [
            _delta(0, "Ho"),
            fake.sse(
                1,
                {
                    "event": "run.failed",
                    "run_id": "run_1",
                    "error": "model error",
                    "usage": usage,
                    "runtime": hermes.runtime,
                },
            ),
        ]
    ]
    with pytest.raises(RunFailed) as info:
        _drain(_stream(make_run_client(hermes)))
    assert info.value.kind == "error"
    assert info.value.run_id == "run_1"
    assert info.value.usage == usage
    assert info.value.runtime == hermes.runtime
    assert "run.failed" in str(info.value) and "model error" in str(info.value)


@pytest.mark.parametrize("event", ["run.cancelled", "run.interrupted"])
def test_stream_every_failure_event_raises(hermes: FakeHermes, event: str) -> None:
    hermes.event_scripts = [[fake.sse(0, {"event": event, "run_id": "run_1"})]]
    with pytest.raises(RunFailed) as info:
        _drain(_stream(make_run_client(hermes)))
    assert info.value.kind == "error" and event in str(info.value)


def test_stream_closing_early_falls_back_to_the_poll(hermes: FakeHermes) -> None:
    hermes.event_scripts = [[": open\n\n", _delta(0, "Recibido"), ": stream closed\n\n"]]
    deltas, result = _drain(_stream(make_run_client(hermes)))
    assert deltas == ["Recibido"]
    assert result.status == "completed" and result.output == "Recibido (IA): hola"
    assert result.usage == hermes.usage
    assert hermes.polls == 2  # one "running", then the final status
    assert len(hermes.posts) == 1


def test_stream_closing_early_on_a_failed_run_raises_from_the_poll(hermes: FakeHermes) -> None:
    hermes.final_status = "failed"
    hermes.event_scripts = [[": open\n\n"]]
    with pytest.raises(RunFailed) as info:
        _drain(_stream(make_run_client(hermes)))
    assert "run failed" in str(info.value)


def test_stream_events_not_found_falls_back_to_the_poll(hermes: FakeHermes) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/events"):
            return httpx.Response(404, json={"error": "not_found"})
        return hermes.handler(request)

    client = RunClient(
        base_url=BASE_URL,
        profile=PROFILE,
        api_key=API_KEY,
        timeout_s=5.0,
        poll_interval_s=0,
        http=httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handler)),
    )
    deltas, result = _drain(_stream(client))
    assert deltas == []
    assert result.output == "Recibido (IA): hola"


def test_stream_raises_timeout_at_the_deadline(hermes: FakeHermes) -> None:
    hermes.event_scripts = [[": open\n\n"] + [": keepalive\n\n"] * 100]
    ticks = iter(range(0, 1000))
    client = make_run_client(
        hermes, timeout_s=5.0, monotonic=lambda: float(next(ticks)), sleep=lambda s: None
    )
    with pytest.raises(RunFailed) as info:
        _drain(_stream(client))
    assert info.value.kind == "timeout"
    assert info.value.run_id == "run_1"
    assert info.value.latency_ms >= 5000
    assert hermes.polls == 0


def test_stream_drop_with_no_time_left_raises_timeout(hermes: FakeHermes) -> None:
    hermes.event_scripts = [[fake.drop()] for _ in range(3)]
    ticks = iter(range(0, 1000))
    client = make_run_client(
        hermes, timeout_s=2.0, monotonic=lambda: float(next(ticks)), sleep=lambda s: None
    )
    with pytest.raises(RunFailed) as info:
        _drain(_stream(client))
    assert info.value.kind == "timeout"
    assert "stream dropped" in str(info.value)
    assert len(hermes.event_requests) == 1
    assert len(hermes.posts) == 1


def test_stream_never_reconnects_once_the_deadline_has_passed(hermes: FakeHermes) -> None:
    hermes.event_scripts = [[fake.drop()] for _ in range(3)]
    ticks = iter(range(0, 1000))
    client = make_run_client(
        hermes, timeout_s=3.0, monotonic=lambda: float(next(ticks)), sleep=lambda s: None
    )
    with pytest.raises(RunFailed) as info:
        _drain(_stream(client))
    assert info.value.kind == "timeout"
    assert len(hermes.event_requests) == 1
    assert len(hermes.posts) == 1
    assert hermes.polls == 0


def test_stream_read_timeout_is_capped_at_15s_and_the_time_left(hermes: FakeHermes) -> None:
    _drain(_stream(make_run_client(hermes, timeout_s=60.0)))
    assert hermes.event_requests[0]["timeout"]["read"] == 15.0

    short = FakeHermes()
    clock = iter([0.0] + [2.0] * 100)
    _drain(_stream(make_run_client(short, timeout_s=5.0, monotonic=lambda: next(clock))))
    assert short.event_requests[0]["timeout"]["read"] == 3.0


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        (lambda h: setattr(h, "key", "other"), "HTTP 401"),
        (lambda h: setattr(h, "down", True), "unreachable"),
        (lambda h: setattr(h, "accepted_run_id", 7), "valid run_id"),
    ],
)
def test_stream_start_errors_raise_as_run_does(hermes: FakeHermes, setup, message: str) -> None:
    setup(hermes)
    with pytest.raises(RunFailed) as info:
        _drain(_stream(make_run_client(hermes)))
    assert info.value.kind == "error"
    assert message in str(info.value)
    assert hermes.event_requests == []


def test_stream_parses_multiline_data_odd_ids_and_a_last_frame_without_a_blank_line(
    hermes: FakeHermes,
) -> None:
    completed = json.dumps({"event": "run.completed", "run_id": "run_1", "output": "Hola"})
    hermes.event_scripts = [
        [
            'id: 0\ndata: {"event": "message.delta",\ndata:  "run_id": "run_1", "delta": "Ho"}\n\n',
            'id: x\ndata: {"event": "message.delta", "run_id": "run_1", "delta": "la"}\n\n',
            "id: 1\ndata: not json\n\n",
            "event: ignored\nretry: 10\n\n",
            f"id: 2\ndata: {completed}",
        ]
    ]
    deltas, result = _drain(_stream(make_run_client(hermes)))
    assert deltas == ["Ho", "la"]
    assert result.output == "Hola"
    assert result.usage == {} and result.runtime == {}


# --------------------------------------------------------------------------- ADR 0073


def _client(handler: Any, **kwargs: Any) -> RunClient:
    return RunClient(
        base_url=BASE_URL,
        profile=PROFILE,
        api_key=API_KEY,
        poll_interval_s=0,
        http=httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handler)),
        **kwargs,
    )


def test_a_refused_connection_fails_at_once_with_one_post(hermes: FakeHermes) -> None:
    """Fail fast: a refused connection never reached Hermes, so there is nothing to wait for."""
    seen: list[str] = []

    def spy(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        return hermes.handler(request)

    hermes.down = True
    with pytest.raises(RunFailed) as info:
        _client(spy).run(session_id="a", input="x", idempotency_key="k")
    assert seen == ["POST"]
    assert "unreachable" in str(info.value) and info.value.final is False


def test_a_lost_post_answer_is_posted_again_under_the_same_key(hermes: FakeHermes) -> None:
    """Hermes took the run but its answer was lost: the second POST gets that run back."""
    lost = {"n": 0}

    def lose_first_answer(request: httpx.Request) -> httpx.Response:
        response = hermes.handler(request)
        if request.method == "POST" and lost["n"] == 0:
            lost["n"] += 1
            raise httpx.ReadTimeout("timed out reading the answer", request=request)
        return response

    result = _client(lose_first_answer).run(session_id="a", input="hola", idempotency_key="k")
    assert result.run_id == "run_1" and result.output == "Recibido (IA): hola"
    assert [p["idempotency_key"] for p in hermes.posts] == ["k", "k"]
    assert len(hermes.runs) == 1


def test_a_post_whose_answer_is_lost_twice_fails_and_is_not_final(hermes: FakeHermes) -> None:
    def always_lost(request: httpx.Request) -> httpx.Response:
        hermes.handler(request)
        raise httpx.RemoteProtocolError("connection closed", request=request)

    with pytest.raises(RunFailed) as info:
        _client(always_lost).run(session_id="a", input="x", idempotency_key="k")
    assert info.value.final is False  # the next attempt re-POSTs under "k"
    assert len(hermes.posts) == 2


def test_a_run_hermes_reports_failed_is_final_and_a_refusal_too(hermes: FakeHermes) -> None:
    hermes.final_status = "failed"
    with pytest.raises(RunFailed) as failed:
        make_run_client(hermes).run(session_id="a", input="x", idempotency_key="k")
    assert failed.value.final is True
    hermes.final_status = "completed"
    hermes.key = "other"
    with pytest.raises(RunFailed) as refused:
        make_run_client(hermes).run(session_id="a", input="x", idempotency_key="k2")
    assert refused.value.final is True  # 401: our request is wrong, a new key changes nothing


def test_a_failure_event_on_the_stream_is_final(hermes: FakeHermes) -> None:
    hermes.event_scripts = [[fake.sse(0, {"event": "run.failed", "run_id": "run_1"})]]
    with pytest.raises(RunFailed) as info:
        list(make_run_client(hermes).stream(session_id="a", input="x", idempotency_key="k"))
    assert info.value.final is True


def test_a_server_error_at_post_is_not_final() -> None:
    def busy(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "busy"})

    with pytest.raises(RunFailed) as info:
        _client(busy).run(session_id="a", input="x", idempotency_key="k")
    assert info.value.final is False


def test_the_poll_gives_up_when_hermes_is_unreachable_not_the_full_timeout(
    hermes: FakeHermes,
) -> None:
    def down_after_post(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            raise httpx.ConnectError("connection refused", request=request)
        return hermes.handler(request)

    ticks = iter(range(0, 1000))
    client = _client(
        down_after_post, timeout_s=60.0, monotonic=lambda: float(next(ticks)), sleep=lambda s: None
    )
    with pytest.raises(RunFailed) as info:
        client.run(session_id="a", input="x", idempotency_key="k")
    assert "unreachable while polling" in str(info.value)
    assert info.value.kind == "error" and info.value.run_id == "run_1"
    assert info.value.latency_ms < 10_000  # seconds, not the 60 s timeout


def test_the_poll_rides_out_a_short_blip(hermes: FakeHermes) -> None:
    blips = {"n": 0}

    def one_blip(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and blips["n"] == 0:
            blips["n"] += 1
            raise httpx.ConnectError("connection refused", request=request)
        return hermes.handler(request)

    ticks = iter(range(0, 1000))
    client = _client(one_blip, monotonic=lambda: float(next(ticks)), sleep=lambda s: None)
    assert client.run(session_id="a", input="x", idempotency_key="k").status == "completed"


def test_the_poll_waits_for_a_slow_run_hermes_still_reports_running(hermes: FakeHermes) -> None:
    """A legitimately slow run is never cut short by the fail-fast rule."""
    hermes.running_polls = 30
    ticks = iter(range(0, 1000))
    client = make_run_client(
        hermes, timeout_s=60.0, monotonic=lambda: float(next(ticks)), sleep=lambda s: None
    )
    assert client.run(session_id="a", input="x", idempotency_key="k").status == "completed"


def test_a_success_after_a_failed_attempt_of_the_same_run_replaces_its_row(
    conn, hermes: FakeHermes
) -> None:
    candidate, _ = repo.get_or_create_candidate(
        conn, client_id="grupo_sazon", channel="webchat", external_id="wc_y", country="ES"
    )
    app = repo.start_application(conn, candidate["id"], "webchat", now=NOW)
    timeout = RunFailed("timed out after 60s (run still running)", kind="timeout", run_id="run_1")
    record_usage(
        conn, application=app, profile=PROFILE, environment="test", now=NOW, failure=timeout
    )
    result = make_run_client(hermes).run(session_id=app["id"], input="x", idempotency_key="k")
    for _ in range(2):
        record_usage(
            conn, application=app, profile=PROFILE, environment="test", now=NOW, result=result
        )
    rows = conn.execute("SELECT status, input_tokens, error FROM usage").fetchall()
    assert [tuple(r) for r in rows] == [("ok", 120, None)]


# --------------------------------------------------------------------------- stop on timeout
# Hermes v0.21.6 has `POST /v1/runs/{run_id}/stop` (`gateway/platforms/api_server_runs.py:240`,
# handler `:1253-1274`). A run the client stops waiting for is stopped upstream, best effort.


def _timing_out(hermes: FakeHermes, **kwargs: Any) -> RunClient:
    """A client whose run times out after five polls that answer "running"."""
    hermes.running_polls = 10_000
    ticks = iter(range(0, 1000))
    return make_run_client(
        hermes, timeout_s=5.0, monotonic=lambda: float(next(ticks)), sleep=lambda s: None, **kwargs
    )


def _app(conn, external_id: str) -> dict[str, Any]:
    candidate, _ = repo.get_or_create_candidate(
        conn, client_id="grupo_sazon", channel="webchat", external_id=external_id, country="ES"
    )
    return repo.start_application(conn, candidate["id"], "webchat", now=NOW)


def test_a_timed_out_run_is_stopped_upstream_once_with_a_short_timeout(
    hermes: FakeHermes,
) -> None:
    with pytest.raises(RunFailed) as info:
        _timing_out(hermes).run(session_id="a", input="x", idempotency_key="k")
    assert info.value.kind == "timeout" and info.value.run_id == "run_1"
    # Not final: a retry under the same key replays the run, whatever it became (ADR 0073).
    assert info.value.final is False
    assert [s["run_id"] for s in hermes.stops] == ["run_1"]
    assert hermes.stops[0]["timeout"]["read"] == STOP_TIMEOUT_S
    assert STOP_TIMEOUT_S <= 5.0
    assert "stop requested" in str(info.value)
    assert info.value.usage == {}  # Hermes reports none for a stopping run


def test_a_streamed_run_that_times_out_is_stopped_upstream(hermes: FakeHermes) -> None:
    hermes.event_scripts = [[": open\n\n"] + [": keepalive\n\n"] * 100]
    ticks = iter(range(0, 1000))
    client = make_run_client(
        hermes, timeout_s=5.0, monotonic=lambda: float(next(ticks)), sleep=lambda s: None
    )
    with pytest.raises(RunFailed) as info:
        _drain(_stream(client))
    assert info.value.kind == "timeout" and info.value.final is False
    assert [s["run_id"] for s in hermes.stops] == ["run_1"]


def test_a_stream_drop_past_the_deadline_stops_the_run(hermes: FakeHermes) -> None:
    hermes.event_scripts = [[fake.drop()] for _ in range(3)]
    ticks = iter(range(0, 1000))
    client = make_run_client(
        hermes, timeout_s=2.0, monotonic=lambda: float(next(ticks)), sleep=lambda s: None
    )
    with pytest.raises(RunFailed) as info:
        _drain(_stream(client))
    assert info.value.kind == "timeout"
    assert [s["run_id"] for s in hermes.stops] == ["run_1"]


@pytest.mark.parametrize(
    "setup",
    [
        lambda h: setattr(h, "stop_error", httpx.ConnectError("refused")),
        lambda h: setattr(h, "stop_error", httpx.ReadTimeout("stop timed out")),
        lambda h: setattr(h, "stop_error", RuntimeError("transport bug")),
        lambda h: setattr(h, "stop_status_code", 500),
        lambda h: setattr(h, "stop_status_code", 409),  # run_not_active
        lambda h: setattr(h, "stop_status_code", 404),
    ],
)
def test_a_failed_stop_never_raises_and_the_caller_still_gets_the_timeout(
    hermes: FakeHermes, setup
) -> None:
    setup(hermes)
    with pytest.raises(RunFailed) as info:
        _timing_out(hermes).run(session_id="a", input="x", idempotency_key="k")
    assert info.value.kind == "timeout" and info.value.run_id == "run_1"
    assert info.value.final is False
    assert info.value.latency_ms >= 5000
    assert info.value.usage == {}
    assert "stop failed" in str(info.value)
    assert len(hermes.stops) == 1


def test_a_failed_stop_on_the_stream_still_gives_the_timeout(hermes: FakeHermes) -> None:
    hermes.stop_error = httpx.ReadTimeout("stop timed out")
    hermes.event_scripts = [[": open\n\n"] + [": keepalive\n\n"] * 100]
    ticks = iter(range(0, 1000))
    client = make_run_client(
        hermes, timeout_s=5.0, monotonic=lambda: float(next(ticks)), sleep=lambda s: None
    )
    with pytest.raises(RunFailed) as info:
        _drain(_stream(client))
    assert info.value.kind == "timeout" and info.value.final is False


def test_usage_the_stop_answer_carries_lands_on_the_ledger_row(conn, hermes: FakeHermes) -> None:
    """The run finished between the last poll and the stop: Hermes answers the stop with the
    run's status as it stands (`api_server_runs.py:1262-1263`), usage and model included."""

    def finished_before_the_stop(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/stop"):
            hermes.running_polls = 0
        return hermes.handler(request)

    hermes.running_polls = 10_000
    ticks = iter(range(0, 1000))
    client = RunClient(
        base_url=BASE_URL,
        profile=PROFILE,
        api_key=API_KEY,
        timeout_s=5.0,
        poll_interval_s=0,
        monotonic=lambda: float(next(ticks)),
        sleep=lambda s: None,
        http=httpx.Client(
            base_url=BASE_URL, transport=httpx.MockTransport(finished_before_the_stop)
        ),
    )
    app = _app(conn, "wc_stop_usage")
    with pytest.raises(RunFailed) as info:
        client.run(session_id=app["id"], input="x", idempotency_key="k")
    assert info.value.kind == "timeout" and info.value.final is False
    assert info.value.usage["input_tokens"] == 120
    record_usage(
        conn, application=app, profile=PROFILE, environment="test", now=NOW, failure=info.value
    )
    row = conn.execute(
        "SELECT status, model, provider, input_tokens, output_tokens, cache_read_tokens,"
        " est_cost_usd, error FROM usage"
    ).fetchone()
    assert (row["status"], row["model"], row["provider"]) == (
        "timeout",
        "anthropic/claude-haiku-5.5",
        "openrouter",
    )
    assert (row["input_tokens"], row["output_tokens"], row["cache_read_tokens"]) == (120, 40, 7000)
    assert row["est_cost_usd"] > 0
    assert "usage unknown" not in row["error"]


def test_a_stopped_run_without_usage_is_recorded_as_usage_unknown_not_zero(
    conn, hermes: FakeHermes
) -> None:
    app = _app(conn, "wc_stop_unknown")
    with pytest.raises(RunFailed) as info:
        _timing_out(hermes).run(session_id=app["id"], input="x", idempotency_key="k")
    record_usage(
        conn, application=app, profile=PROFILE, environment="test", now=NOW, failure=info.value
    )
    row = conn.execute("SELECT status, input_tokens, run_id, error FROM usage").fetchone()
    assert (row["status"], row["input_tokens"], row["run_id"]) == ("timeout", 0, "run_1")
    assert row["error"].startswith(USAGE_UNKNOWN)


def test_a_failure_that_never_reached_hermes_is_not_flagged_usage_unknown(
    conn, hermes: FakeHermes
) -> None:
    """No run_id: Hermes took no run, so the zero counts are the truth."""
    hermes.down = True
    app = _app(conn, "wc_stop_down")
    with pytest.raises(RunFailed) as info:
        make_run_client(hermes).run(session_id=app["id"], input="x", idempotency_key="k")
    record_usage(
        conn, application=app, profile=PROFILE, environment="test", now=NOW, failure=info.value
    )
    error = conn.execute("SELECT error FROM usage").fetchone()["error"]
    assert USAGE_UNKNOWN not in error


def test_a_run_finishing_at_the_last_poll_before_the_deadline_is_not_stopped(
    hermes: FakeHermes,
) -> None:
    # Five polls fit before the 5 s deadline: the fifth answers completed.
    hermes.running_polls = 4
    ticks = iter(range(0, 1000))
    client = make_run_client(
        hermes, timeout_s=5.0, monotonic=lambda: float(next(ticks)), sleep=lambda s: None
    )
    assert client.run(session_id="a", input="x", idempotency_key="k").status == "completed"
    assert hermes.stops == []


def test_a_run_one_poll_slower_times_out_and_is_stopped(hermes: FakeHermes) -> None:
    hermes.running_polls = 5
    ticks = iter(range(0, 1000))
    client = make_run_client(
        hermes, timeout_s=5.0, monotonic=lambda: float(next(ticks)), sleep=lambda s: None
    )
    with pytest.raises(RunFailed):
        client.run(session_id="a", input="x", idempotency_key="k")
    assert len(hermes.stops) == 1


@pytest.mark.parametrize(
    "setup",
    [
        lambda h: None,  # success
        lambda h: setattr(h, "refuse_status", 429),
        lambda h: setattr(h, "final_status", "failed"),
        lambda h: setattr(h, "down", True),
        lambda h: setattr(h, "key", "other"),
    ],
)
def test_no_stop_unless_the_run_timed_out(hermes: FakeHermes, setup) -> None:
    setup(hermes)
    try:
        make_run_client(hermes).run(session_id="a", input="x", idempotency_key="k")
    except RunFailed as exc:
        assert exc.kind == "error"
    hermes.down = False
    assert hermes.stops == []


def test_a_429_refusal_is_final_and_not_stopped(hermes: FakeHermes) -> None:
    hermes.refuse_status = 429
    with pytest.raises(RunFailed) as info:
        make_run_client(hermes).run(session_id="a", input="x", idempotency_key="k")
    assert info.value.final is True and info.value.run_id is None
    assert hermes.stops == []


def test_a_stream_success_is_not_stopped(hermes: FakeHermes) -> None:
    _drain(_stream(make_run_client(hermes)))
    assert hermes.stops == []


def test_a_retry_of_a_stopped_run_replays_it_and_reports_it_final(hermes: FakeHermes) -> None:
    """The owed-turn retry (ADR 0073) re-POSTs the same key: Hermes replays the stopped run,
    now `cancelled`, so the failure is final and the next attempt takes a new key."""
    with pytest.raises(RunFailed):
        _timing_out(hermes).run(session_id="a", input="x", idempotency_key="k")
    with pytest.raises(RunFailed) as info:
        make_run_client(hermes).run(session_id="a", input="x", idempotency_key="k")
    assert info.value.final is True and info.value.kind == "error"
    assert "cancelled" in str(info.value)
    assert len(hermes.runs) == 1 and len(hermes.stops) == 1


def test_a_failure_after_a_recorded_success_keeps_the_counts_known(
    conn, hermes: FakeHermes
) -> None:
    """The run completed and its row holds the counts; storing the reply then failed."""
    app = _app(conn, "wc_known_then_failed")
    result = make_run_client(hermes).run(session_id=app["id"], input="x", idempotency_key="k")
    record_usage(conn, application=app, profile=PROFILE, environment="test", now=NOW, result=result)
    failure = RunFailed("storing the reply failed", run_id=result.run_id)
    record_usage(
        conn, application=app, profile=PROFILE, environment="test", now=NOW, failure=failure
    )
    row = conn.execute("SELECT status, input_tokens, error FROM usage").fetchone()
    assert (row["status"], row["input_tokens"], row["error"]) == (
        "error",
        120,
        "storing the reply failed",
    )


def test_the_replayed_cancelled_run_keeps_its_row_usage_unknown(conn, hermes: FakeHermes) -> None:
    app = _app(conn, "wc_unknown_replayed")
    with pytest.raises(RunFailed) as first:
        _timing_out(hermes).run(session_id=app["id"], input="x", idempotency_key="k")
    with pytest.raises(RunFailed) as second:
        make_run_client(hermes).run(session_id=app["id"], input="x", idempotency_key="k")
    for failure in (first.value, second.value):
        record_usage(
            conn, application=app, profile=PROFILE, environment="test", now=NOW, failure=failure
        )
    rows = conn.execute("SELECT status, input_tokens, error FROM usage").fetchall()
    assert len(rows) == 1
    assert (rows[0]["status"], rows[0]["input_tokens"]) == ("error", 0)
    assert rows[0]["error"].startswith(f"{USAGE_UNKNOWN} run cancelled")
