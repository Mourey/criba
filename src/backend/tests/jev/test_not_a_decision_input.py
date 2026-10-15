"""Signals are never an input to a decision (ADR 0051, 0029).

Two checks. A static one: only the modules that show signals to a Recruiter, or keep them,
may name the ``message_signals`` table or import the store; the Knockout, verification,
Approval, booking and routing code may not. A behavioural one: the same Candidate screened with
every signal at its worst and with none reaches the same outcome, stage and Interview.
"""

from __future__ import annotations

import re
from pathlib import Path

from criba.db import repo
from criba.jev import store
from criba.jev.battery import Reading
from criba.tools import call_tool

ROOT = Path(__file__).resolve().parents[2] / "criba"

# The modules allowed to read or write the table, with why.
ALLOWED = {
    "jev/store.py": "the table's own module",
    "db/repo/signals.py": "the table's SQL, called only from jev/store.py",
    "jev/turn.py": "writes a message's signals",
    "jev/notes.py": "communication notes for the Recruiter",
    "api/present.py": "per-message flags on the Application detail",
    "api/metrics.py": "the aggregate view",
    "scheduler/service.py": "the retention purge",
    "summary.py": "the communication notes in the summary",
    "api/applications.py": "the Application detail",
    # ADR 0066: Operators see the stored probabilities, display only, never an input to a decision.
    "api/readings.py": "the Operator-only Jev readings endpoint (ADR 0066)",
    "api/routes.py": "registers the readings router, nothing else (ADR 0066)",
}
DECISION_PATHS = (
    "screening",
    "tools/application.py",
    "tools/knockout.py",
    "tools/booking.py",
    "tools/choices.py",
    "tools/common.py",
    "channel/router.py",
    "channel/sender.py",
    "api/queue.py",
    "api/claims.py",
    "api/slots.py",
    "api/takeover.py",
    "scheduler/jobs.py",
)
NAMES = re.compile(
    r"message_signals|jev\.store|jev import store|list_signals|record_signals"
    # ADR 0066: the readings reader, imported as a module, a name or in a parenthesised list.
    r"|\bapi\.readings\b|\bapi import (?:[^\n(]*|\([^)]*)\breadings\b"
)


def source(path: str) -> str:
    target = ROOT / path
    files = sorted(target.rglob("*.py")) if target.is_dir() else [target]
    return "\n".join(f.read_text(encoding="utf-8") for f in files)


def test_only_the_listed_modules_name_the_signals_table() -> None:
    users = {
        str(p.relative_to(ROOT))
        for p in ROOT.rglob("*.py")
        if NAMES.search(p.read_text(encoding="utf-8"))
    }
    assert users <= set(ALLOWED), sorted(users - set(ALLOWED))


def test_no_decision_path_reads_the_signals() -> None:
    for path in DECISION_PATHS:
        assert not NAMES.search(source(path)), path
        assert "criba.jev.notes" not in source(path), path
        assert "decide_language" not in source(path) and "decide_tone" not in source(path), path


def screen(conn, clock, *, worst: bool) -> dict:
    """A Mexican Candidate screened to Qualified through the tools, with the worst possible
    signals stored on every message when ``worst``."""
    candidate, _ = repo.get_or_create_candidate(
        conn,
        client_id="grupo_sazon",
        channel="webchat",
        external_id="w" if worst else "c",
        phone="+525500000001" if worst else "+525500000002",
        country="MX",
        language="es-MX",
        now=clock.now(),
    )
    app, _ = repo.resolve_application(conn, candidate["id"], "webchat", now=clock.now())
    message, _ = repo.add_message(
        conn,
        candidate_id=candidate["id"],
        application_id=app["id"],
        channel="webchat",
        direction="inbound",
        sender="candidate",
        body="Hola",
        now=clock.now(),
    )
    if worst:
        store.record_signals(
            conn,
            message_id=message["id"],
            application=app,
            now=clock.now(),
            status="ok",
            reading=Reading(
                "en",
                1.0,
                1.0,
                {"frustrated": 1.0, "confused": 1.0, "wants_human": 1.0, "abusive": 1.0},
                intent="steering",
                intent_confidence=1.0,
                malice=1.0,
            ),
            fired=("abusive", "wants_human", "frustrated", "confused"),
            hint="calm",
            escalation="abuse",
        )
    for field, value in (
        ("full_name", "Luis Hernández"),
        ("zone", ["zone_mx_puebla_centro"]),
        ("driver_license", "yes"),
        ("availability", ["full_time"]),
        ("preferred_schedule", ["morning"]),
        ("experience_years", 2),
        ("experience_platforms", ["rappi"]),
        ("start_date", "2026-10-12"),
    ):
        status, body = call_tool(
            conn, "record_answer", {"field": field, "value": value}, app["id"], now=clock.now()
        )
        assert status == 200 and body["ok"], body
    status, body = call_tool(conn, "finish_screening", {}, app["id"], now=clock.now())
    assert status == 200 and body["ok"], body
    return repo.require_application(conn, app["id"])


def test_the_same_candidate_gets_the_same_outcome_whatever_the_signals(conn, clock) -> None:
    calm = screen(conn, clock, worst=False)
    worst = screen(conn, clock, worst=True)
    for field in ("status", "stage", "outcome", "knockout_criterion", "driver_license"):
        assert worst[field] == calm[field], field
    assert (worst["stage"], worst["status"]) == ("qualified", "open")


# --------------------------------------------------------------------------- the readings reader
# ADR 0066: Operators read the stored probabilities through ``api/readings.py``. The reader and
# its registration in ``api/routes.py`` are the only additions to ALLOWED, and a module that
# decides anything may not import the reader.

READER_IMPORTS = (
    "import criba.api.readings",
    "from criba.api.readings import router",
    "from criba.api import readings",
    "from criba.api import applications, readings",
    "from criba.api import (\n    applications,\n    claims,\n    readings,\n    takeover,\n)",
)
OTHER_API_IMPORTS = (
    "import criba.api.queue",
    "from criba.api import applications, queue",
    "from criba.api import (\n    applications,\n    queue,\n)",
    "from criba.api.present import readings_style",
)


def test_the_regex_matches_every_import_form_of_the_readings_reader() -> None:
    for line in READER_IMPORTS:
        assert NAMES.search(line), line


def test_the_regex_leaves_other_api_imports_alone() -> None:
    for line in OTHER_API_IMPORTS:
        assert not NAMES.search(line), line


def test_the_reader_and_its_registration_are_the_modules_added_for_adr_0066() -> None:
    added = {"api/readings.py", "api/routes.py"}
    assert added <= set(ALLOWED)
    for path in added:
        assert "0066" in ALLOWED[path], path
    assert len(ALLOWED) == 11


def test_the_reader_and_the_router_registering_it_are_found_by_the_guard() -> None:
    assert NAMES.search(source("api/readings.py"))
    assert NAMES.search(source("api/routes.py"))


def test_the_worst_intent_and_malice_are_stored_and_the_outcome_is_the_same(conn, clock) -> None:
    """ADR 0071: the worst reading above carries intent steering and malice 1.0, and they are
    really stored; the outcome is still that of the calm Candidate."""
    calm = screen(conn, clock, worst=False)
    worst = screen(conn, clock, worst=True)
    rows = conn.execute("SELECT intent, intent_confidence, malice FROM message_signals").fetchall()
    assert [tuple(r) for r in rows] == [("steering", 1.0, 1.0)]
    for field in ("status", "stage", "outcome", "knockout_criterion", "driver_license"):
        assert worst[field] == calm[field], field
