"""With the Jev router on, the agent's own language report no longer changes the stored language
(ADR 0051, amending 0042 and 0047). With it off, the report works as before."""

from __future__ import annotations

from pathlib import Path

import pytest

from criba.db import repo
from criba.tools import call_tool

from ..channel.conftest import candidate_for, start


@pytest.fixture
def jev_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    key = tmp_path / "key.txt"
    key.write_text("k-test", encoding="utf-8")
    monkeypatch.setenv("CRIBA_OPENROUTER_API_KEY_FILE", str(key))
    monkeypatch.delenv("CRIBA_JEV", raising=False)
    monkeypatch.delenv("CRIBA_ENV", raising=False)


@pytest.fixture
def jev_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRIBA_OPENROUTER_API_KEY_FILE", raising=False)
    monkeypatch.delenv("CRIBA_JEV", raising=False)


def get_application(conn, app_id: str, clock, **args):
    status, body = call_tool(conn, "get_application", args, app_id, now=clock.now())
    assert status == 200 and body["ok"], body
    return body["value"]


def application(client, conn, locale: str = "es-MX") -> dict:
    conv = start(client, country="MX", locale=locale)
    client.post(
        f"/api/chat/conversations/{conv}/messages", json={"message_id": "m1", "text": "Hola"}
    )
    app = repo.get_open_application(conn, candidate_for(conn, conv)["id"])
    assert app is not None
    return app


def test_the_agents_language_report_is_ignored_when_jev_decides(
    client, conn, clock, jev_on
) -> None:
    app = application(client, conn)
    result = get_application(conn, app["id"], clock, language="en")
    assert result["language"] == "es-MX"  # the stored language, whatever the agent passed
    assert repo.require_application(conn, app["id"])["language"] == "es-MX"
    assert repo.get_candidate(conn, app["candidate_id"])["language"] == "es-MX"


def test_the_stored_language_is_what_the_result_reports_when_jev_decides(
    client, conn, clock, jev_on
) -> None:
    app = application(client, conn)
    repo.set_candidate_language(conn, app["candidate_id"], "en", now=clock.now())  # Jev's decision
    assert get_application(conn, app["id"], clock)["language"] == "en"
    assert get_application(conn, app["id"], clock, language="es-MX")["language"] == "en"


def test_without_jev_the_agents_report_still_switches(client, conn, clock, jev_off) -> None:
    app = application(client, conn)
    assert get_application(conn, app["id"], clock, language="en")["language"] == "en"
    assert repo.require_application(conn, app["id"])["language"] == "en"
    assert get_application(conn, app["id"], clock, language="es-ES")["language"] == "es-MX"
