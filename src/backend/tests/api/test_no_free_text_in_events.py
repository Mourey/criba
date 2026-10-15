"""No Candidate free text in the event log (Q38), end to end.

A Candidate goes through answers (an ambiguous Zone, three invalid answers), a Knockout,
a Review request, an Escalation, a Takeover with hand-back, a withdrawal and an Erasure
request, on real code paths: inbound messages through the channel router, the agent's
tool calls through ``call_tool`` from a fake Hermes (no model), and the Recruiter's side
through the REST API. Every Candidate message and every note carries a unique marker
word. The words must land in ``messages``, ``queue_items`` and ``handbacks``, never in
any event's ``data``: then an Erasure request deletes or anonymises those rows and the
event log needs no scrubbing.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from criba.app import create_app
from criba.channel.config import ChannelSettings
from criba.channel.service import Channel
from criba.db import repo
from criba.db.connection import connect
from criba.tools import call_tool
from criba.tools import http as tools_http

from ..channel.conftest import FakeHermes, Fakes, make_run_client
from .support import NOW, OPERATOR, PUEBLA_CENTRO, UNCOVERED_ZONE, Env

# One marker per Candidate message or note. Unusual enough never to occur by chance.
MARKERS = [f"qzx{name}" for name in (
    "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india",
    "juliet", "kilo", "lima", "mike", "november", "oscar", "papa", "quebec", "romeo",
    "zulu",
)]  # fmt: skip
# The Candidate's name carries its own marker: it lives on the record, never in an event.
NAME = "Luis Qzxzulu Hernández"
M = dict(zip([m.removeprefix("qzx") for m in MARKERS], MARKERS, strict=True))

Step = Callable[[str], str]  # session_id -> the agent's reply


class Agent:
    """A scripted agent: each run pops the next step, which calls tools and replies."""

    def __init__(self, env: Env, hermes: FakeHermes) -> None:
        self.env = env
        self.hermes = hermes
        self.steps: list[Step] = []
        self.results: list[dict[str, Any]] = []
        hermes.reply = self._reply

    def _reply(self, _input: str) -> str:
        session_id = self.hermes.posts[-1]["body"]["session_id"]
        return self.steps.pop(0)(session_id)

    def tool(self, session_id: str, name: str, **args: Any) -> dict[str, Any]:
        conn = connect(self.env.db_path)
        try:
            status, body = call_tool(conn, name, args, session_id, now=self.env.clock.now())
        finally:
            conn.close()
        assert status == 200, body
        self.results.append(body)
        return body

    def then(self, step: Step) -> None:
        self.steps.append(step)


@pytest.fixture
def hermes() -> FakeHermes:
    return FakeHermes()


@pytest.fixture
def channel(env: Env, hermes: FakeHermes) -> Channel:
    fakes = Fakes()
    return Channel(
        connect=lambda: connect(env.db_path),
        run_client=make_run_client(hermes),
        clock=env.clock,
        settings=ChannelSettings(db_path=str(env.db_path), environment="test"),
        pending_choice=fakes.pending_choice,
        interview_offer=fakes.interview_offer,
    )


def test_no_candidate_words_reach_any_event(env: Env, channel: Channel, hermes: FakeHermes):
    env.clock.set(NOW + timedelta(hours=8))  # midday in Mexico City
    candidate, _ = repo.get_or_create_candidate(
        env.conn,
        client_id="grupo_sazon",
        channel="webchat",
        external_id=uuid.uuid4().hex,
        country="MX",
        language="es-MX",
        now=env.clock.now(),
    )
    agent = Agent(env, hermes)
    operator = env.client(OPERATOR)

    def say(text: str) -> dict[str, Any]:
        """The Candidate writes; the agent runs if it holds the chat. Returns the result."""
        env.clock.advance(seconds=30)
        with channel.connection() as conn:
            fresh = repo.get_candidate(conn, candidate["id"])
            assert fresh is not None
            result = channel.router.accept_inbound(
                conn, fresh, channel="webchat", message_id=uuid.uuid4().hex, text=text
            )
        if result.turn is not None:
            channel.run_turn(result.turn)
        return {"application_id": result.application_id, "reason": result.reason}

    def open_item(app_id: str, kind: str) -> str:
        row = env.conn.execute(
            "SELECT id FROM queue_items WHERE application_id = ? AND kind = ? AND status = 'open'",
            (app_id, kind),
        ).fetchone()
        assert row is not None, (app_id, kind)
        return row[0]

    # --- Screening: a name, an ambiguous Zone, its choice --------------------------------
    agent.then(lambda s: agent.tool(s, "record_answer", field="full_name", value=NAME)
               and "¿En qué zona vives?")  # fmt: skip
    app_id = say(f"Hola, soy {NAME} {M['alpha']}")["application_id"]
    # The name is on the record, and its answer_recorded event says only that it was.
    assert repo.require_application(env.conn, app_id)["full_name"] == NAME
    assert repo.get_candidate(env.conn, candidate["id"])["full_name"] == NAME
    puebla = ["zone_mx_puebla_angelopolis", PUEBLA_CENTRO, "zone_mx_puebla_la_paz"]
    agent.then(lambda s: agent.tool(s, "record_answer", field="zone", value=puebla)
               and "¿Cuál zona de Puebla?")  # fmt: skip
    say(f"Puebla {M['bravo']}")
    agent.then(lambda s: agent.tool(s, "record_answer", field="zone", value=[PUEBLA_CENTRO])
               and "¿Tienes licencia?")  # fmt: skip
    say(f"Centro {M['charlie']}")

    # --- The Knockout: the agent sends the template text it got back ----------------------
    def knock_out(s: str) -> str:
        body = agent.tool(s, "record_answer", field="driver_license", value="no")
        assert body["value"]["status"] == "knockout", body
        return body["value"]["message"]

    agent.then(knock_out)
    say(f"no {M['delta']}")
    app = repo.require_application(env.conn, app_id)
    assert (app["status"], app["outcome"]) == ("closed", "knockout")

    # --- A Review request, overturned by the Operator (the Application reopens) -----------
    agent.then(lambda s: agent.tool(s, "request_review", note=f"Dice que sí tiene {M['echo']}")
               and "Una persona lo revisará.")  # fmt: skip
    say(f"Sí tengo licencia, revisión {M['echo']}")
    review = open_item(app_id, "review")
    response = operator.post(f"/api/queue/reviews/{review}", json={"decision": "overturned"})
    assert response.status_code == 200, response.text

    # --- Three invalid answers: the third raises an Escalation quoting the last ----------
    for word in ("foxtrot", "golf", "hotel"):
        agent.then(lambda s, w=word: agent.tool(
            s, "record_answer", field="availability", value=None
        ) and "¿Tiempo completo, medio tiempo o fines de semana?")  # fmt: skip
        say(f"quién sabe {M[word]}")
    escalation = open_item(app_id, "escalation")

    # --- Takeover, a Candidate message under it, a generated hand-back -------------------
    response = operator.post(f"/api/queue/escalations/{escalation}", json={"decision": "takeover"})
    assert response.status_code == 200, response.text
    env.clock.advance(seconds=30)
    response = operator.post(
        f"/api/applications/{app_id}/takeover/messages", json={"text": "Hola, te ayudo yo."}
    )
    assert response.status_code == 201, response.text
    assert say(f"Gracias, trabajo los sábados {M['india']}")["reason"] == "human_control"
    env.clock.advance(seconds=30)
    response = operator.post(f"/api/applications/{app_id}/takeover/handback")
    assert response.status_code == 200, response.text

    # The agent's next turn reads the hand-back summary (from handbacks, not the event).
    seen: dict[str, Any] = {}

    def after_handback(s: str) -> str:
        seen.update(agent.tool(s, "get_application")["value"])
        agent.tool(s, "raise_escalation", reason="wants_human", note=f"Pide humano {M['juliet']}")
        return "Le aviso al equipo."

    agent.then(after_handback)
    say(f"¿Sigo con el bot? Quiero una persona {M['juliet']}")
    assert seen["takeover_summary"] and M["india"] in seen["takeover_summary"]

    # --- Withdrawal -----------------------------------------------------------------------
    agent.then(lambda s: agent.tool(
        s, "record_backout", kind="withdrawn", note=f"Encontró otro trabajo {M['kilo']}"
    ) and "Entendido, gracias.")  # fmt: skip
    say(f"Ya no me interesa, encontré otro trabajo {M['kilo']}")
    assert repo.require_application(env.conn, app_id)["outcome"] == "withdrawn"

    # --- An Erasure request on a new Application --------------------------------------------
    agent.then(lambda s: agent.tool(
        s, "record_backout", kind="erasure_request", note=f"Borrar datos {M['lima']}"
    ) and "Lo paso al equipo.")  # fmt: skip
    second = say(f"Borren mis datos por favor {M['lima']}")["application_id"]
    assert repo.get_candidate(env.conn, candidate["id"])["erasure_requested_at"]

    assert not agent.steps, "every scripted turn ran"
    # Every call succeeded except the three misses (invalid_value is the tool's answer).
    failed = [r for r in agent.results if not r["ok"]]
    assert [r["error"] for r in failed] == ["invalid_value"] * 3, failed

    # ======================================================================================
    # The proof: no event's data holds any marker, stored or served.
    # ======================================================================================
    rows = env.conn.execute(
        "SELECT id, kind, data FROM events WHERE candidate_id = ? ORDER BY id", (candidate["id"],)
    ).fetchall()
    kinds = {r["kind"] for r in rows}
    assert {
        "answer_recorded",
        "answer_ambiguous",
        "answer_invalid",
        "knockout",
        "review_requested",
        "review_overturned",
        "reopened",
        "escalation_raised",
        "takeover_started",
        "takeover_handed_back",
        "withdrawn",
        "erasure_requested",
    } <= kinds, kinds
    leaks = [
        (r["id"], r["kind"], marker)
        for r in rows
        for marker in MARKERS
        if marker in r["data"].lower()
    ]
    assert not leaks, leaks
    for app in (app_id, second):
        served = operator.get(f"/api/applications/{app}").json()["events"]
        text = json.dumps([e["data"] for e in served], ensure_ascii=False).lower()
        assert not [m for m in MARKERS if m in text]

    # The words are where they belong: messages, queue items, hand-backs.
    bodies = " ".join(
        r[0] for r in env.conn.execute(
            "SELECT body FROM messages WHERE candidate_id = ?", (candidate["id"],)
        )
    )  # fmt: skip
    used = [M[k] for k in ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf",
                           "hotel", "india", "juliet", "kilo", "lima")]  # fmt: skip
    assert NAME in bodies
    assert all(m in bodies for m in used)
    notes = " ".join(
        r[0] or "" for r in env.conn.execute(
            "SELECT note FROM queue_items WHERE application_id = ?", (app_id,)
        )
    )  # fmt: skip
    assert M["echo"] in notes and M["hotel"] in notes and M["juliet"] in notes
    [handback] = env.conn.execute(
        "SELECT summary FROM handbacks WHERE application_id = ?", (app_id,)
    ).fetchall()
    assert M["india"] in handback[0]

    # References by id: answers point at the inbound message, the Knockout at its reply.
    data = {r["id"]: (r["kind"], json.loads(r["data"])) for r in rows}
    named = next(
        d for k, d in data.values() if k == "answer_recorded" and d["field"] == "full_name"
    )
    assert "value" not in named and named["message_id"]
    knockout = next(d for k, d in data.values() if k == "knockout")
    reply = env.conn.execute(
        "SELECT * FROM messages WHERE id = ?", (knockout["message_id"],)
    ).fetchone()
    assert reply["sender"] == "agent" and reply["body"].startswith("Gracias, Luis.")
    assert set(knockout) == {"reason", "template", "criterion", "stage", "message_id"}
    invalid = [d for k, d in data.values() if k == "answer_invalid"]
    assert len(invalid) == 3
    for event in invalid:
        message = env.conn.execute(
            "SELECT direction, body FROM messages WHERE id = ?", (event["message_id"],)
        ).fetchone()
        assert message["direction"] == "inbound" and "quién sabe" in message["body"]
    handed_back = next(d for k, d in data.values() if k == "takeover_handed_back")
    assert set(handed_back) == {"handback_id", "generated", "held_by"}
    assert handed_back["generated"] is True


def test_structured_answers_keep_the_candidates_words_out_of_events(
    env: Env, channel: Channel, hermes: FakeHermes
):
    """ADR 0042: the agent passes ids, codes and null; the events hold those and message ids.
    The Candidate's words reach zone_input and driver_license_input from their message."""
    env.clock.set(NOW + timedelta(hours=8))
    candidate, _ = repo.get_or_create_candidate(
        env.conn,
        client_id="grupo_sazon",
        channel="webchat",
        external_id=uuid.uuid4().hex,
        country="MX",
        language="es-MX",
        now=env.clock.now(),
    )
    agent = Agent(env, hermes)

    def say(text: str) -> str:
        env.clock.advance(seconds=30)
        with channel.connection() as conn:
            fresh = repo.get_candidate(conn, candidate["id"])
            assert fresh is not None
            result = channel.router.accept_inbound(
                conn, fresh, channel="webchat", message_id=uuid.uuid4().hex, text=text
            )
        assert result.turn is not None and result.application_id is not None
        channel.run_turn(result.turn)
        return result.application_id

    def step(field: str, value: Any, reply: str) -> None:
        agent.then(lambda s: agent.tool(s, "record_answer", field=field, value=value) and reply)

    step("full_name", "Luis Hernández", "¿En qué zona vives?")
    app_id = say(f"Soy Luis Hernández {M['mike']}")
    step("zone", [UNCOVERED_ZONE], "¿Vives en Milpa Alta?")
    say(f"por Milpa Alta {M['november']}")
    step("zone", None, "¿En qué colonia vives entonces?")
    say(f"No {M['oscar']}")
    step("zone", [PUEBLA_CENTRO], "¿Tienes licencia?")
    say(f"en el centro de Puebla {M['papa']}")
    step("driver_license", None, "¿Tienes licencia vigente, sí o no?")
    say(f"mmm depende {M['quebec']}")
    step("driver_license", "yes", "¿Qué disponibilidad tienes?")
    say(f"sí la tengo {M['romeo']}")

    assert not agent.steps
    statuses = [r["value"].get("status") if r["ok"] else r["reason"] for r in agent.results]
    assert statuses == [
        "accepted",
        "needs_choice",
        "zone_not_confirmed",
        "accepted",
        "unresolved",
        "accepted",
    ]
    rows = env.conn.execute(
        "SELECT kind, data FROM events WHERE application_id = ? ORDER BY id", (app_id,)
    ).fetchall()
    assert not [(r["kind"], m) for r in rows for m in MARKERS if m in r["data"].lower()]
    assert {"answer_recorded", "answer_ambiguous", "answer_invalid"} <= {r["kind"] for r in rows}
    [miss] = [json.loads(r["data"]) for r in rows if r["kind"] == "answer_invalid"]
    assert miss["reason"] == "unresolved" and miss["message_id"]
    app = repo.require_application(env.conn, app_id)
    assert app["zone_id"] == PUEBLA_CENTRO and app["driver_license"] is True
    assert app["zone_input"] == f"en el centro de Puebla {M['papa']}"
    assert app["driver_license_input"] == f"sí la tengo {M['romeo']}"


# --- the same two journeys, with the agent's tools routed through POST /internal/tools ----------

TOOLS_SECRET = "test-tools-secret"


class HttpAgent(Agent):
    """The scripted agent, calling each tool as the Hermes plugin does: over HTTP, through the
    tool trace. Every call is counted, and the tests check the trace holds one row for each."""

    def __init__(self, env: Env, hermes: FakeHermes) -> None:
        super().__init__(env, hermes)
        app = create_app()
        app.dependency_overrides[tools_http.get_connection_factory] = lambda: (
            lambda: connect(env.db_path)
        )
        app.dependency_overrides[tools_http.get_clock] = lambda: env.clock
        self.http = TestClient(app)
        self.calls = 0

    def tool(self, session_id: str, name: str, **args: Any) -> dict[str, Any]:
        response = self.http.post(
            f"/internal/tools/{name}",
            json={"tool": name, "args": args, "session_id": session_id},
            headers={"Authorization": f"Bearer {TOOLS_SECRET}"},
        )
        self.calls += 1
        assert response.status_code == 200, response.text
        body = response.json()
        self.results.append(body)
        return body


@pytest.mark.parametrize(
    "journey",
    [
        test_no_candidate_words_reach_any_event,
        test_structured_answers_keep_the_candidates_words_out_of_events,
    ],
    ids=["events", "structured_answers"],
)
def test_the_tool_trace_holds_every_call_linked_to_its_run_and_no_free_text(
    journey: Callable[..., None],
    env: Env,
    channel: Channel,
    hermes: FakeHermes,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secret = tmp_path / "criba_tools_secret.txt"
    secret.write_text(TOOLS_SECRET + "\n", encoding="utf-8")
    monkeypatch.setenv("CRIBA_TOOLS_SECRET_FILE", str(secret))
    agents: list[HttpAgent] = []

    def scripted_agent(env: Env, hermes: FakeHermes) -> HttpAgent:
        agents.append(HttpAgent(env, hermes))
        return agents[-1]

    monkeypatch.setitem(globals(), "Agent", scripted_agent)
    journey(env, channel, hermes)

    calls = sum(agent.calls for agent in agents)
    stored = env.conn.execute("SELECT run_id, args FROM tool_calls ORDER BY id").fetchall()
    assert calls > 0
    # M5: one row for every call the agent made...
    assert len(stored) == calls
    # ...each linked to its run once the runs returned...
    assert [r["run_id"] for r in stored if r["run_id"] is None] == []
    # ...and no Candidate word or name in any argument (ADR 0035).
    text = " ".join(r["args"] for r in stored).lower()
    assert not [m for m in MARKERS if m in text]
    assert not [part for part in NAME.lower().split() if part in text]
