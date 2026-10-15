"""A Rejection made on the Application's own page reaches open queue streams.

When the Application has no open Approval, the Rejection opens and resolves no queue item, so
the queue stream announces it from the Application's ``rejection`` event, in the same ``queue``
frame type and under the same Location scoping.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from criba.api import queue as queue_module
from criba.api.auth import COOKIE_NAME, hash_token, load_session_user
from criba.api.routing import OPERATOR_ONLY_KEYS
from criba.db.connection import iso

from .support import LA_PAZ, OPERATOR, PUEBLA, PUEBLA_ANGELOPOLIS
from .test_cost_fields import keys_in
from .test_takeover import parse_sse, read_stream

NOTE = "Documentos falsos"
FRAME_KEYS = {"kind", "status", "application_id"}


def reject_directly(env, app_id, *, as_user=OPERATOR):
    def act():
        response = env.client(as_user).post(
            f"/api/applications/{app_id}/rejection", json={"reason": "other", "note": NOTE}
        )
        assert response.status_code == 200, response.text

    return act


def queue_frames(env, as_user, act):
    # The fixture clock stands still: move past the setup, whose Approvals resolve "now".
    env.clock.advance(seconds=5)
    return read_stream(env.client(as_user), "/api/queue/stream", act=act)


@pytest.mark.parametrize("watcher", [OPERATOR, PUEBLA])
def test_direct_rejection_reaches_operators_and_covering_recruiters(env, build, contract, watcher):
    app = build.application(stage="approved")
    frames = queue_frames(env, watcher, reject_directly(env, app["id"], as_user=PUEBLA))
    assert [f["event"] for f in frames] == ["queue"]
    contract.validate("StreamEvent", frames[0]["data"])
    assert frames[0]["data"]["type"] == "queue"
    assert frames[0]["data"]["data"] == {
        "kind": "rejection",
        "status": "closed",
        "application_id": app["id"],
    }


def test_direct_rejection_is_invisible_to_a_recruiter_outside_its_locations(env, build):
    outside = build.application(stage="approved", zone=PUEBLA_ANGELOPOLIS)
    inside = build.application(stage="approved")

    def act():
        reject_directly(env, outside["id"])()
        reject_directly(env, inside["id"])()

    frames = queue_frames(env, LA_PAZ, act)
    assert [f["data"]["data"]["application_id"] for f in frames] == [inside["id"]]
    assert outside["id"] not in json.dumps(frames)


def test_direct_rejection_frame_carries_no_free_text_or_cost_keys(env, build):
    app = build.application(stage="approved")
    frames = queue_frames(env, OPERATOR, reject_directly(env, app["id"]))
    assert len(frames) == 1
    assert set(frames[0]["data"]["data"]) == FRAME_KEYS
    assert not keys_in(frames[0]["data"]) & OPERATOR_ONLY_KEYS
    assert NOTE not in json.dumps(frames)


def test_rejection_that_resolves_an_approval_is_announced_once(env, build):
    app = build.application()
    frames = queue_frames(env, OPERATOR, reject_directly(env, app["id"]))
    assert [(f["data"]["data"]["kind"], f["data"]["data"]["status"]) for f in frames] == [
        ("approval", "resolved")
    ]


def test_an_earlier_rejection_is_not_replayed_to_a_new_stream(env, build):
    app = build.application(stage="approved")
    reject_directly(env, app["id"])()
    assert queue_frames(env, OPERATOR, lambda: None) == []


class _RejectAfterEventMark:
    """A connection that commits a Rejection right after the poll reads the newest event id."""

    def __init__(self, conn, reject):
        self._conn = conn
        self._reject = reject
        self._marked = False

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def execute(self, sql, *args):
        if self._marked and self._reject:
            self._reject()
            self._reject = None
        self._marked = "MAX(id), 0) FROM events" in sql
        return self._conn.execute(sql, *args)


def test_a_rejection_committed_during_a_poll_is_announced_once(env, build):
    # Each SELECT in the poll reads its own snapshot, so a Rejection can commit after the
    # newest event id is read and still be returned by the rejection query in that poll.
    app = build.application(stage="approved")
    client = env.client(PUEBLA)
    user = load_session_user(env.ctx, client.cookies[COOKIE_NAME])
    env.clock.advance(seconds=5)
    with env.ctx.db() as conn:
        newest = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM queue_items").fetchone()[0]
        newest_event = conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
    state = {
        "rowid": newest,
        "event_id": newest_event,
        "resolved_since": iso(env.clock.now()),
        "seen_resolved": set(),
    }
    reject = reject_directly(env, app["id"], as_user=PUEBLA)

    @contextmanager
    def racing_db():
        with env.ctx.db() as conn:
            yield _RejectAfterEventMark(conn, reject)

    racing = SimpleNamespace(db=racing_db, session_clock=env.ctx.session_clock)
    token_hash = hash_token(client.cookies[COOKIE_NAME])
    first, alive = queue_module._poll_queue(racing, user, token_hash, state)
    assert alive
    second, _ = queue_module._poll_queue(env.ctx, user, token_hash, state)
    frames = parse_sse("".join(first + second))
    assert [f["data"]["data"] for f in frames] == [
        {"kind": "rejection", "status": "closed", "application_id": app["id"]}
    ]
