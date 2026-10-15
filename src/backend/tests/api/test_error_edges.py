"""Edge inputs that used to overflow into a 500 on ``/api`` (lane B, error envelope): each is a
422 in the contract's ``Error`` shape now, or, for a resume id the server never wrote, ignored as
SSE does."""

from __future__ import annotations

import pytest

from criba.api.applications import MAX_OFFSET, decode_cursor, encode_cursor
from criba.api.errors import ApiError

from .support import OPERATOR, PUEBLA


@pytest.mark.parametrize("operation", ["getResults", "getConversationSignals", "getEconomics"])
@pytest.mark.parametrize(
    ("query", "field"),
    [("to=9999-12-31", "to"), ("to=0001-01-01", "to")],
    ids=["last-day", "first-day"],
)
def test_a_period_at_the_edge_of_the_calendar_is_a_422(
    env, contract, operation: str, query: str, field: str
) -> None:
    # The metrics operations list no 422 (from after to was one already): the body is the
    # contract's Error all the same.
    url, _, _ = contract.operations[operation]
    resp = env.client(OPERATOR).get(f"{url}?{query}")
    assert resp.status_code == 422
    body = resp.json()
    contract.validate("Error", body)
    assert body["error"] == "validation_error"
    assert body["details"] == [{"field": field, "message": body["message"]}]


def test_a_period_ending_the_day_before_the_last_still_works(env, contract) -> None:
    resp = env.client(OPERATOR).get("/api/metrics/results?from=9999-12-01&to=9999-12-30")
    assert resp.status_code == 200
    contract.conforms(resp, "getResults")


def test_created_to_on_the_last_day_is_a_422(env, contract) -> None:
    resp = env.client(OPERATOR).get("/api/applications?created_to=9999-12-31")
    body = contract.conforms(resp, "listApplications")
    assert resp.status_code == 422
    assert body["details"][0]["field"] == "created_to"


@pytest.mark.parametrize("offset", [10**20, MAX_OFFSET + 1])
def test_a_cursor_past_int64_is_a_422(env, contract, offset: int) -> None:
    resp = env.client(OPERATOR).get(f"/api/applications?cursor={encode_cursor(offset)}")
    body = contract.conforms(resp, "listApplications")
    assert resp.status_code == 422
    assert body["details"][0]["field"] == "cursor"


def test_the_largest_cursor_is_an_empty_page(env, contract) -> None:
    assert decode_cursor(encode_cursor(MAX_OFFSET)) == MAX_OFFSET
    resp = env.client(OPERATOR).get(f"/api/applications?cursor={encode_cursor(MAX_OFFSET)}")
    body = contract.conforms(resp, "listApplications")
    assert body["items"] == []


def test_decode_cursor_refuses_a_negative_offset() -> None:
    with pytest.raises(ApiError):
        decode_cursor(encode_cursor(-1))


@pytest.mark.parametrize(
    "last_event_id",
    ["q-²", "q-" + "9" * 40, "q-", "q-12 x", "x-12"],
    ids=["superscript", "forty-digits", "empty", "trailing-text", "other-kind"],
)
def test_the_queue_stream_ignores_a_last_event_id_it_never_wrote(env, last_event_id: str) -> None:
    with env.client(PUEBLA).stream(
        "GET", "/api/queue/stream", headers={"Last-Event-ID": last_event_id.encode("latin-1")}
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        text = "".join(response.iter_text())
    assert text.startswith(": connected")


@pytest.mark.parametrize(
    "last_event_id",
    ["m-" + "9" * 30, "e-99999999999999999999", "m-²", "m-12 x"],
    ids=["thirty-digits", "twenty-digits", "superscript", "trailing-text"],
)
def test_the_relay_ignores_a_last_event_id_it_never_wrote(env, build, last_event_id) -> None:
    app = build.application()
    with env.client(PUEBLA).stream(
        "GET",
        f"/api/applications/{app['id']}/stream",
        headers={"Last-Event-ID": last_event_id.encode("latin-1")},
    ) as response:
        assert response.status_code == 200
        text = "".join(response.iter_text())
    assert text.startswith(": connected")
