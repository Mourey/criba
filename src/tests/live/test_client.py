"""`live.client.LiveStack` against the in-process fake: turns, taps, queue paging, the Operator
API calls, the summary wait and what a conversation is charged."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest

from live.client import LiveStack, Turn, summary_prose
from live.fake_api import HAIKU, SONNET, FakeApi, cost_block, make_application, outbound
from sim.spend import SpendGuard


def run_with[T](
    api: FakeApi,
    body: Callable[[LiveStack], Awaitable[T]],
    *,
    cap: float = 10.0,
    poll_wait_s: float = 0.0,
    reply_timeout_s: float = 5.0,
) -> tuple[T, SpendGuard]:
    guard = SpendGuard(cap_usd=cap, real=True)

    async def go() -> T:
        transport = httpx.ASGITransport(app=api.app)
        async with (
            httpx.AsyncClient(transport=transport, base_url="http://fake") as chat,
            httpx.AsyncClient(transport=transport, base_url="http://fake") as operator,
        ):
            stack = LiveStack(
                chat, operator, guard, reply_timeout_s=reply_timeout_s, poll_wait_s=poll_wait_s
            )
            return await body(stack)

    return asyncio.run(go()), guard


def test_read_turn_collects_the_burst_once_the_run_has_finished() -> None:
    api = FakeApi()
    api.finished = [False, False, True]

    def on_message(a: FakeApi, cid: str, text: str) -> None:
        a.say(cid, "first")
        a.say(cid, "second")

    api.on_message = on_message

    async def body(stack: LiveStack) -> Turn:
        cid, cursor = await stack.start("ES", "es-ES", "+34600000001")
        return await stack.say(cid, "hola", cursor)

    turn, _ = run_with(api, body)
    assert [m["text"] for m in turn.messages] == ["first", "second"]
    assert turn.finished and turn.first_s is not None and turn.last_s is not None
    assert turn.first_s <= turn.last_s
    assert turn.cursor == 3  # our message, then the two replies
    assert api.reads == 3


def test_read_turn_gives_up_at_the_timeout_with_nothing() -> None:
    api = FakeApi()
    api.finished = [False] * 500

    async def body(stack: LiveStack) -> Turn:
        cid, cursor = await stack.start("ES", "es-ES", "+34600000001")
        return await stack.read_turn(cid, cursor, timeout_s=0.05)

    turn, _ = run_with(api, body)
    assert turn.messages == [] and turn.first_s is None and not turn.finished


def test_turn_helpers_find_options_and_attachments() -> None:
    offer = outbound(
        "pick", interactive={"style": "list", "options": [{"id": "so_1", "label": "a"}]}
    )
    invite = outbound("done", attachments=[{"type": "ics", "url": "/files/x.ics"}])
    turn = Turn([outbound("hi"), offer, invite], 1.0, 2.0, 3, True)
    assert turn.interactive() is offer
    assert turn.attachments("ics") == [{"type": "ics", "url": "/files/x.ics"}]
    assert turn.attachments("pdf") == []
    assert Turn([], None, None, 0, True).interactive() is None


def test_tap_sends_the_option_and_reads_the_answer() -> None:
    api = FakeApi()
    taps: list[tuple[int, str]] = []

    def on_reply(a: FakeApi, cid: str, in_reply_to: int, option: str) -> None:
        taps.append((in_reply_to, option))
        a.say(cid, "booked", attachments=[{"type": "ics", "url": "/files/x.ics"}])

    api.on_reply = on_reply

    async def body(stack: LiveStack) -> Turn:
        cid, _ = await stack.start("ES", "es-ES", "+34600000001")
        return await stack.tap(cid, 7, "so_abc")

    turn, _ = run_with(api, body)
    assert taps == [(7, "so_abc")]
    assert [m["text"] for m in turn.messages] == ["booked"]
    assert len(turn.attachments("ics")) == 1


def test_download_fetches_a_file() -> None:
    api = FakeApi()
    api.files["x.ics"] = ("text/calendar", "BEGIN:VCALENDAR")

    async def body(stack: LiveStack) -> httpx.Response:
        return await stack.download("/files/x.ics")

    response, _ = run_with(api, body)
    assert response.headers["content-type"].startswith("text/calendar")
    assert response.text == "BEGIN:VCALENDAR"


def test_application_waits_for_the_summary_prose() -> None:
    api = FakeApi()
    api.applications["app_1"] = make_application()
    api.summary_after_reads = 2

    async def body(stack: LiveStack) -> tuple[dict[str, Any], dict[str, Any]]:
        quick = await stack.application("+34600000001")
        waited = await stack.application("+34600000001", with_summary=True, timeout_s=10)
        return quick, waited

    (quick, waited), _ = run_with(api, body)
    assert summary_prose(quick) == ""  # the first read is before the prose landed
    assert summary_prose(waited) == "A short summary of the Candidate."


def test_application_without_waiting_returns_whatever_is_there() -> None:
    api = FakeApi()
    api.applications["app_1"] = make_application(prose=None)

    async def body(stack: LiveStack) -> dict[str, Any]:
        return await stack.application("+34600000001", with_summary=True, timeout_s=0)

    app, _ = run_with(api, body)
    assert summary_prose(app) == "" and "cost" in app


def test_application_lookup_error_when_none_matches() -> None:
    async def body(stack: LiveStack) -> dict[str, Any]:
        return await stack.application("+34600000009")

    with pytest.raises(LookupError):
        run_with(FakeApi(), body)


def test_applications_for_lists_only_that_phone() -> None:
    api = FakeApi()
    api.applications["app_1"] = make_application("app_1", "+34600000001")
    api.applications["app_2"] = make_application("app_2", "+34600000002")

    async def body(stack: LiveStack) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        return (
            await stack.applications_for("+34600000001"),
            await stack.applications_for("+34600000077"),
        )

    (one, none), _ = run_with(api, body)
    assert [a["id"] for a in one] == ["app_1"] and none == []


def test_queue_items_follow_the_pages_and_keep_one_applications_items() -> None:
    api = FakeApi()
    api.queue_pages = [
        [{"id": "qi_1", "application": {"id": "app_other"}}],
        [{"id": "qi_2", "application": {"id": "app_1"}}],
    ]

    async def body(stack: LiveStack) -> list[dict[str, Any]]:
        return await stack.queue_items("app_1", "approval")

    items, _ = run_with(api, body)
    assert [i["id"] for i in items] == ["qi_2"]


def test_approve_posts_the_decision_and_detail_reads_the_application() -> None:
    api = FakeApi()
    api.applications["app_1"] = make_application()

    async def body(stack: LiveStack) -> tuple[httpx.Response, dict[str, Any]]:
        return await stack.approve("qi_9"), await stack.detail("app_1")

    (response, detail), _ = run_with(api, body)
    assert response.status_code == 200 and api.approvals == ["qi_9"]
    assert detail["id"] == "app_1"


def test_charge_counts_the_conversation_against_the_cap() -> None:
    api = FakeApi()
    api.applications["app_1"] = make_application(cost=cost_block())

    async def body(stack: LiveStack) -> Any:
        return stack.charge(await stack.application("+34600000001"))

    charge, guard = run_with(api, body)
    assert charge.models == [HAIKU, SONNET]
    assert charge.runs == 4
    # Haiku: 4,000 uncached in at 0.10, 6,000 cached at 0.01, 500 out at 0.50 per million tokens;
    # the Sonnet summary is OpenRouter's own charge.
    haiku = (4_000 * 0.10 + 6_000 * 0.01 + 500 * 0.50) / 1_000_000
    assert charge.cost_usd == pytest.approx(haiku + 0.0085)
    assert charge.billed_usd == pytest.approx(0.0085)
    assert charge.tokens["input_tokens"] == 13_000
    assert guard.spent_usd == pytest.approx(charge.guard_usd)
    assert charge.guard_usd >= charge.cost_usd - 1e-9
