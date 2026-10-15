"""The run events stream against the real Hermes image (spec R8.2): Hermes sends
``message.delta`` then ``run.completed`` on ``/p/<profile>/v1/runs/{run_id}/events``, and
``RunClient.stream`` from the backend container returns the completed run with the joined
deltas equal to its output.

It makes real runs on the stub model (src/tests/sim/stub_llm), so it needs the sim stack
(``docker-compose.sim.yml``) with the backend, which the criba-tools plugin forwards tool calls
to, and is enabled with CRIBA_HERMES_SIM=1::

    CRIBA_WEB_PORT=18134 docker compose -p criba-voicestream-sim \\
        -f docker-compose.yml -f docker-compose.sim.yml up -d --build --wait backend hermes
    COMPOSE_PROJECT_NAME=criba-voicestream-sim CRIBA_REQUIRE_HERMES=1 CRIBA_HERMES_SIM=1 \\
        uv run --project src/backend pytest src/backend/tests/hermes/test_run_events.py -q -m hermes
    docker compose -p criba-voicestream-sim -f docker-compose.yml -f docker-compose.sim.yml down -v

Both halves run inside the containers (nothing is published to the host). The Hermes API key
is read there from the Compose secret and never leaves the container; only event names,
counts and booleans come back to the test.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import pytest

from .conftest import HermesContainer

pytestmark = [
    pytest.mark.hermes,
    pytest.mark.skipif(
        os.environ.get("CRIBA_HERMES_SIM") != "1",
        reason="needs the sim stack (stub model, backend); enable with CRIBA_HERMES_SIM=1",
    ),
]

PROFILE = "criba-recruiter-sim"
SAY = "Hola, quiero trabajar de repartidor."

# Runs inside the hermes container: one run, then its events read to the end of the stream.
RAW_EVENTS = """
import json, sys, urllib.request
a = json.loads(sys.argv[1])
key = open("/run/secrets/hermes_api_server_key").read().strip()
base = "http://127.0.0.1:8642/p/" + a["profile"] + "/v1/runs"
post = urllib.request.Request(base, method="POST",
    data=json.dumps({"input": a["input"], "session_id": a["session_id"]}).encode())
post.add_header("Authorization", "Bearer " + key)
post.add_header("Content-Type", "application/json")
post.add_header("Idempotency-Key", a["idempotency_key"])
answer = urllib.request.urlopen(post, timeout=30)
post_status, run_id = answer.status, json.loads(answer.read())["run_id"]
events = urllib.request.Request(base + "/" + run_id + "/events")
events.add_header("Authorization", "Bearer " + key)
events.add_header("Accept", "text/event-stream")
names, foreign, with_id = [], 0, 0
pending_id = False
for raw in urllib.request.urlopen(events, timeout=90):
    line = raw.decode("utf-8").rstrip("\\r\\n")
    if line.startswith("id:"):
        pending_id = True
    elif line.startswith("data:"):
        data = json.loads(line[5:])
        names.append(data.get("event"))
        foreign += data.get("run_id") != run_id
        with_id += pending_id
    elif line == "":
        pending_id = False
print(json.dumps({"post_status": post_status, "events": names, "foreign": foreign,
                  "with_id": with_id}))
"""

# Runs inside the backend container: RunClient.stream as the backend would build it.
CLIENT_STREAM = """
import json, sys
from criba.channel.config import ChannelSettings
from criba.channel.run_client import RunClient
a = json.loads(sys.argv[1])
settings = ChannelSettings.from_env()
client = RunClient(base_url=settings.hermes_url, profile=settings.hermes_profile,
    api_key=settings.read_api_key(), timeout_s=settings.run_timeout_s)
stream = client.stream(session_id=a["session_id"], input=a["input"],
    idempotency_key=a["idempotency_key"])
deltas = []
while True:
    try:
        deltas.append(next(stream))
    except StopIteration as stop:
        result = stop.value
        break
print(json.dumps({"profile": settings.hermes_profile, "deltas": len(deltas),
    "status": result.status, "joined_is_output": "".join(deltas).strip() == result.output,
    "has_output": bool(result.output), "has_usage": bool(result.usage)}))
"""


def _last_json(out: str) -> dict[str, Any]:
    lines = [line for line in out.splitlines() if line.strip()]
    parsed: dict[str, Any] = json.loads(lines[-1])
    return parsed


def _args(session_id: str) -> str:
    return json.dumps(
        {
            "profile": PROFILE,
            "session_id": session_id,
            "input": SAY,
            "idempotency_key": f"msg_{uuid.uuid4().hex}",
        }
    )


def test_events_stream_deltas_then_run_completed(hermes_container: HermesContainer) -> None:
    suffix = uuid.uuid4().hex[:8]

    # Raw events, as Hermes sends them.
    raw = _last_json(
        hermes_container.exec(
            "python3", "-c", RAW_EVENTS, _args(f"app_stream_raw_{suffix}"), timeout=120
        )
    )
    assert raw["post_status"] == 202, raw
    names: list[str] = raw["events"]
    deltas = [i for i, name in enumerate(names) if name == "message.delta"]
    assert deltas, names
    assert names[-1] == "run.completed", names
    assert names.count("run.completed") == 1, names
    assert all(i < len(names) - 1 for i in deltas), names
    assert raw["foreign"] == 0, raw
    assert raw["with_id"] == len(names), raw  # every frame carries its sequence id

    # The client, from the backend container.
    got = _last_json(
        hermes_container.compose(
            "exec",
            "-T",
            "backend",
            "python",
            "-c",
            CLIENT_STREAM,
            _args(f"app_stream_client_{suffix}"),
            timeout=120,
        )
    )
    assert got["profile"] == PROFILE, got
    assert got["status"] == "completed", got
    assert got["deltas"] >= 1, got
    assert got["has_output"] and got["joined_is_output"], got
    assert got["has_usage"], got
