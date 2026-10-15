"""Spike 1's leak scenario as a regression test (ADR 0010, spec 002 task 14): one Candidate's
facts must never reach another Candidate's session.

It makes real runs on the stub model (src/tests/sim/stub_llm), so it needs the sim stack
(``docker-compose.sim.yml``) and is enabled with CRIBA_RUN_ISOLATION=1 (W3-1)::

    CRIBA_WEB_PORT=18100 docker compose -p criba-w3-1 \\
        -f docker-compose.yml -f docker-compose.sim.yml up -d --build --wait
    COMPOSE_PROJECT_NAME=criba-w3-1 CRIBA_RUN_ISOLATION=1 CRIBA_REQUIRE_HERMES=1 \\
        uv run --project src/backend pytest src/backend/tests/hermes/test_session_isolation.py -q

It uses the simulator profile (``CRIBA_HERMES_PROFILE`` overrides) so no paid provider is
involved. Candidate A states a name and a place and asks to be remembered; then Candidate B,
in a new session, asks for "my" name and place. The checks:

1. Hermes wrote nothing about A into the profile's memory files (the spike's leak path).
2. B's stored transcript (user turns and tool results) holds none of A's facts.
3. B's model requests, from the stub's request log (``/__stub/requests``), hold none of A's
   facts: system prompt, history, tool schemas and tool results, everything the model reads.
   This is the check that catches memory or session search being on (ADR 0010), whatever
   the model then says.
4. B's reply holds none of A's facts. Meaningful because the stub's scripted replies never
   repeat a Candidate's own words (``screener.yaml``), so a fact in a reply could only come
   from a leak.

A's facts are unusual words chosen for this test: the Screener skill's own examples use
"Ana Ruiz" and "Chamberí", which every request carries in its system prompt. Each check is
shown to be non-vacuous: the facts are in A's own transcript and A's own model request.

On defaults the spike saw the ``memory`` tool write "Ana ... Chamberí" to USER.md, and a
different session then repeated it. With memory and session_search disabled, session B
answered "unknown".
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

import pytest

from .conftest import HERMES_DATA, HermesContainer

pytestmark = [
    pytest.mark.hermes,
    pytest.mark.skipif(
        os.environ.get("CRIBA_RUN_ISOLATION") != "1",
        reason="needs the sim stack and its stub model; enable with CRIBA_RUN_ISOLATION=1",
    ),
]

# Not in SOUL.md, the Screener skill or the tool schemas, so only A's own words carry them.
FACTS_A = ("Ximena", "Zubizarreta", "Chamartín", "Chamartin")
SAY_A = "Me llamo Ximena Zubizarreta, vivo en Chamartín, Madrid. Acuérdate de esto para siempre."
SAY_B = "¿Cómo me llamo y dónde vivo? Dímelo."
STATE_DB_READER = """
import json, sqlite3, sys
db = sqlite3.connect("file:" + sys.argv[1] + "?mode=ro", uri=True)
rows = db.execute(
    "select s.id, m.role, m.content from messages m join sessions s on s.id = m.session_id"
).fetchall()
print(json.dumps(rows))
"""
STUB_REQUESTS = """
import urllib.request
print(urllib.request.urlopen(
    "http://127.0.0.1:8000/__stub/requests?limit=1000", timeout=10).read().decode())
"""


def run_turn(
    hermes: HermesContainer, profile: str, key: str, session_id: str, text: str
) -> dict[str, Any]:
    path = f"/p/{profile}/v1/runs"
    status, started = hermes.http(
        "POST", path, key=key, body={"input": text, "session_id": session_id}
    )
    assert status == 202, (status, started)
    run_id = started["run_id"]
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        status, info = hermes.http("GET", f"{path}/{run_id}", key=key)
        assert status == 200, (status, info)
        if info["status"] in ("completed", "failed", "cancelled"):
            return info
        time.sleep(1)
    pytest.fail(f"run {run_id} did not finish in 90 s")


def stub_requests(hermes: HermesContainer) -> list[dict[str, Any]]:
    """The stub model's request log, read from inside its container (it publishes no port)."""
    out = hermes.compose("exec", "-T", "stub-llm", "python", "-c", STUB_REQUESTS)
    return json.loads(out)["requests"]


def requests_of(requests: list[dict[str, Any]], said: str) -> list[dict[str, Any]]:
    """Model requests made for the session whose Candidate said ``said``: those whose
    conversation (after the system prompt) contains that user message."""
    return [
        r
        for r in requests
        if any(
            m.get("role") == "user" and said in str(m.get("content"))
            for m in r["body"].get("messages", [])
        )
    ]


def leaked(text: str) -> list[str]:
    return [fact for fact in FACTS_A if fact in text]


def test_candidate_a_facts_never_reach_candidate_b(hermes_container: HermesContainer) -> None:
    profile = os.environ.get("CRIBA_HERMES_PROFILE", "criba-recruiter-sim")
    key = hermes_container.secret("hermes_api_server_key")
    suffix = uuid.uuid4().hex[:8]
    session_a, session_b = f"app_iso_a_{suffix}", f"app_iso_b_{suffix}"
    say_b = f"{SAY_B} ({suffix})"  # tells this run's B requests apart in the stub's log

    run_a = run_turn(hermes_container, profile, key, session_a, SAY_A)
    assert run_a["status"] == "completed", run_a
    run_b = run_turn(hermes_container, profile, key, session_b, say_b)
    assert run_b["status"] == "completed", run_b

    # 1. Hermes wrote nothing about A into the profile's memory (the spike's leak path).
    home = f"{HERMES_DATA}/profiles/{profile}"
    memory_files = hermes_container.exec("sh", "-c", f"ls {home}/memories 2>/dev/null || true")
    for name in memory_files.split():
        text = hermes_container.read_file(f"{home}/memories/{name}")
        assert not leaked(text), f"{leaked(text)} saved to memories/{name}"

    # 2. B's stored transcript is B's alone: what Hermes put in front of the model as user
    #    turns and tool results.
    rows = json.loads(
        hermes_container.exec(
            "python3", "-c", STATE_DB_READER, f"{home}/state.db", user="hermes"
        ).strip()
    )
    for session_id, role, content in rows:
        if session_id == session_b and role in ("user", "tool"):
            assert not leaked(content or ""), f"{leaked(content or '')} in B's transcript ({role})"
    # Not vacuous: the facts are in A's own transcript, so the search above can find them.
    assert any(leaked(content or "") for sid, _, content in rows if sid == session_a)
    assert {r[0] for r in rows} >= {session_a, session_b}

    # 3. Every model request made for B holds none of A's facts, anywhere in the body.
    requests = stub_requests(hermes_container)
    of_a, of_b = requests_of(requests, SAY_A), requests_of(requests, say_b)
    assert of_b, "no model request for B in the stub's log"
    for request in of_b:
        body = json.dumps(request["body"], ensure_ascii=False)
        assert not leaked(body), f"{leaked(body)} in B's model request #{request['n']}"
        # B's conversation is B's alone: one user turn, no earlier exchange carried over.
        users = [m for m in request["body"]["messages"] if m.get("role") == "user"]
        assert [str(m.get("content")) for m in users] == [say_b], users
    # Not vacuous: A's own requests carry the facts, so the search above can find them.
    assert of_a and all(leaked(json.dumps(r["body"], ensure_ascii=False)) for r in of_a)

    # 4. B's reply repeats nothing of A (the stub never echoes a Candidate's words).
    assert not leaked(str(run_b.get("output") or "")), run_b.get("output")
