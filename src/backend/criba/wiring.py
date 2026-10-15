"""Integrator wiring: one runtime that the app's routers, the channel and the scheduler share.

:func:`build_runtime` builds, from the environment:

- **one clock**: ``SystemClock``, or in sim mode (``CRIBA_ENV=sim``) the ``SimClock`` the
  sim clock endpoints need. The channel, the scheduler, the summary writer, the tools
  router and the API context all get this same instance.
- **the channel** (W2-B) with W2-A's ``pending_choice`` and the Interview offer from code.
- **the scheduler** (W2-C) with the summary writer: ``DeterministicSummaryClient`` in sim
  and test, ``OpenRouterSummaryClient`` in production.
- **the API context** (W2-D) whose services go through the channel's sender, W2-A's
  booking core and the scheduler.
- **the voice runtime** (voice-agent session-start SE14): the voice settings and the
  ElevenLabs client (the fake in sim and test); in production with voice on, also the
  started agent drift cache running ``agent_drift`` (agents-config, decision 25).

At startup the database is migrated (:func:`prepare_database`); with ``CRIBA_SEED=1`` the
SYNTHETIC Client, Zones, Locations and slots are seeded, plus the synthetic users when a
password is given (``CRIBA_SEED_PASSWORD_FILE``).

The summary is written once Screening ends: on ``finish_screening`` (Qualified), a
Knockout from ``record_answer`` and a withdrawal from ``record_backout`` (the tools
router's after-call hook, :meth:`Runtime.after_tool_call`); on Abandoned by the scheduler
itself; and rewritten when an Application reopens: a Review overturns a Knockout
(``notify``), the Candidate's new valid answer to the failed criterion reopens it through
``record_answer`` (ADR 0033, :func:`reopened_by_answer`), or a chat message reopens an
Abandoned one (the router's ``on_reopened``, :meth:`Runtime.reopened`).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from criba.api.booking import ToolsBookingService
from criba.api.clock import SimClock
from criba.api.context import ApiContext, ApiSettings
from criba.api.errors import conflict, validation_error
from criba.api.messages import notice_language, notice_text
from criba.api.metrics import UsageComparison
from criba.api.services import Notice, Services
from criba.channel.config import ChannelSettings, usage_environment
from criba.channel.outage import KeyCheck, OutageAlarms
from criba.channel.run_client import RunClientProtocol
from criba.channel.scheduling import build_scheduler
from criba.channel.service import Channel
from criba.channel.spoken import SpokenChecker
from criba.channel.voice_agents_config import agent_drift
from criba.channel.voice_config import VoiceSettings
from criba.channel.voice_session import AgentDriftCache, VoiceRuntime, voice_runtime_from_env
from criba.db import connect, migrate, repo
from criba.db.seed import seed, seed_users
from criba.jev import JevSettings, OpenRouterJev
from criba.jev.turn import JevStep
from criba.scheduler import RunReport, Scheduler
from criba.screening.clock import Clock, SystemClock
from criba.summary import (
    DeterministicSummaryClient,
    OpenRouterSummaryClient,
    SummaryClient,
    SummaryWriter,
    Trigger,
    summary_model_from_env,
)
from criba.tools import pending_choice
from criba.tools.booking import offer_interview_slots

log = logging.getLogger("criba.wiring")

OPENROUTER_KEY_FILE_ENV = "CRIBA_OPENROUTER_API_KEY_FILE"
SEED_ENV = "CRIBA_SEED"
SEED_PASSWORD_FILE_ENV = "CRIBA_SEED_PASSWORD_FILE"
SCHEDULER_INTERVAL_ENV = "CRIBA_SCHEDULER_INTERVAL_S"


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


def interview_offer(conn: sqlite3.Connection, application_id: str, now: datetime) -> dict[str, Any]:
    """The channel's ``InterviewOfferFn``: ``offer_interview_slots`` run from code.

    Same refusal as the tool: ``not_approved`` unless Approved in the database (Q41).
    A ``ToolError`` propagates; the router turns it into an Escalation.
    """
    app = repo.require_application(conn, application_id)
    return offer_interview_slots(conn, app, {}, now)


class ChannelCandidateSender:
    """``services.sender`` through the channel's sender, so its guards apply.

    The rule:

    - A Team message typed by a person during a Takeover is a human reply, not a message
      we start: quiet hours do not apply (``proactive=False``), as for any reply.
    - An automated notice follows the rule of the reply it goes with. The
      ``takeover_joined`` notice goes with the team member's replies, so it is not
      proactive either; the Rejection notice is a named person's decision (ADR 0012),
      delivered when they make it, as an approved template.
    - Outside WhatsApp's 24-hour window only a template may go out. A Team text there is
      refused with 409 ``outside_whatsapp_window`` (``contracts/api.openapi.yaml``); a
      free-text notice there is not sent and is logged.
    """

    def __init__(self, channel: Channel) -> None:
        self.channel = channel

    def send_human_text(
        self,
        conn: sqlite3.Connection,
        *,
        application_id: str,
        user_id: str,
        text: str,
        now: datetime,
    ) -> dict[str, Any]:
        app = repo.require_application(conn, application_id)
        result = self.channel.sender.send(
            conn,
            app,
            text=text,
            channel=app["channel"],
            sender="human",
            sender_user_id=user_id,
            proactive=False,
        )
        if result.reason == "outside_window":
            raise conflict(
                "outside_whatsapp_window",
                "The Candidate last wrote more than 24 hours ago, so WhatsApp allows only an "
                "approved template now. The message was not sent; it can go once the "
                "Candidate writes again.",
            )
        if result.status != "sent" or result.message is None:
            raise validation_error(f"The message cannot be sent now ({result.reason}).", "text")
        return result.message

    def send_notice(
        self, conn: sqlite3.Connection, *, application_id: str, notice: Notice, now: datetime
    ) -> dict[str, Any] | None:
        app = repo.require_application(conn, application_id)
        client = repo.client_name(conn, app["client_id"])
        body = notice_text(notice, notice_language(app), client if client else "el equipo")
        result = self.channel.sender.send(
            conn,
            app,
            text=body,
            channel=app["channel"],
            sender="system",
            template_name="rejection" if notice == "rejection" else None,
            proactive=False,
        )
        if result.status != "sent":
            log.warning("notice %s for %s not sent: %s", notice, application_id, result.reason)
        return result.message


def _accepted(body: dict[str, Any]) -> bool:
    return (body.get("value") or {}).get("status") == "accepted"


def summary_trigger(tool: str, body: dict[str, Any]) -> Trigger | None:
    """Whether a successful tool call ended Screening, so the summary is due."""
    value = body.get("value") or {}
    if tool == "finish_screening" and value.get("stage") == "qualified":
        return "finished"
    if tool == "record_answer" and value.get("status") == "knockout":
        return "finished"
    if tool == "record_backout" and value.get("kind") == "withdrawn":
        return "finished" if value.get("status") == "closed" else None
    return None


def reopened_by_answer(conn: sqlite3.Connection, application_id: str) -> bool:
    """Whether the last ``record_answer`` reopened a Knockout (ADR 0033).

    That call writes a ``reopened`` event (``via`` candidate, from a Knockout) directly
    followed by its ``answer_recorded``; any later answer breaks the pair.
    """
    rows = repo.last_answer_or_reopen_events(conn, application_id)
    if [r["kind"] for r in rows] != ["answer_recorded", "reopened"]:
        return False
    data = json.loads(rows[1]["data"])
    return data.get("via") == "candidate" and data.get("previous_outcome") == "knockout"


def summary_client_from_env(env: Mapping[str, str]) -> SummaryClient | None:
    """Deterministic prose in sim and test; Sonnet through OpenRouter in production."""
    if usage_environment(env.get("CRIBA_ENV")) in ("sim", "test"):
        return DeterministicSummaryClient()
    path = env.get(OPENROUTER_KEY_FILE_ENV)
    key = ""
    if path:
        try:
            key = Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            log.exception("cannot read %s", path)
    if not key:
        log.warning("no OpenRouter key (%s): summaries are stored without prose", path)
        return None
    return OpenRouterSummaryClient(key, model=summary_model_from_env(env))


def key_check_from_env(
    env: Mapping[str, str], *, alarms: OutageAlarms, clock: Clock
) -> KeyCheck | None:
    """The hourly OpenRouter key check (ADR 0078), or None in sim and test and when there is no
    key. It uses the key file the summary client and Jev use."""
    if usage_environment(env.get("CRIBA_ENV")) in ("sim", "test"):
        return None
    path = env.get(OPENROUTER_KEY_FILE_ENV)
    try:
        key = Path(path).read_text(encoding="utf-8").strip() if path else ""
    except OSError:
        key = ""
    if not key:
        log.warning("no OpenRouter key (%s): the key balance is not checked", path)
        return None
    return KeyCheck.from_env(env, key=key, alarms=alarms, clock=clock)


def jev_step_from_env(env: Mapping[str, str], *, environment: str) -> JevStep | None:
    """The Jev router's step (ADR 0051), or None when it is off.

    It is on when ``CRIBA_JEV`` allows it and there is a key (outside sim mode) or a stub URL (sim
    mode). With no step the router and the tools behave as before: the agent reports the
    language and no tone is read."""
    settings = JevSettings.from_env(env)
    if not settings.enabled:
        log.info("jev router off (CRIBA_JEV, no key, or sim mode without a stub)")
        return None
    key = settings.read_key()
    if not key and environment != "sim":
        return None
    port = OpenRouterJev(
        api_key=key or "sim-stub",
        model=settings.model,
        url=settings.url,
        timeout_s=settings.timeout_s,
    )
    log.info(
        "jev router on: %s via %s, timeout %.1fs", settings.model, settings.url, settings.timeout_s
    )
    return JevStep(port, environment=environment)


def jobs_run(report: RunReport) -> int:
    """How many things one scheduler run did (the sim clock endpoint's ``due_jobs_run``)."""
    return sum(
        count
        for name, part in report.as_dict().items()
        for key, count in part.items()
        if key != "errors" and not (name == "deferred" and key == "failed")
    )


def in_background(fn: Callable[[], object], name: str) -> None:
    """Run ``fn`` on a daemon thread, logging a failure (the caller already answered)."""

    def run() -> None:
        try:
            fn()
        except Exception:
            log.exception("%s failed", name)

    threading.Thread(target=run, name=name, daemon=True).start()


def prepare_database(env: Mapping[str, str]) -> None:
    """Migrate the database; seed the SYNTHETIC data and users when asked to."""
    conn = connect(env.get("CRIBA_DB_PATH", "/data/criba.db"))
    try:
        applied = migrate(conn)
        if applied:
            log.info("applied migrations %s", applied)
        if env.get(SEED_ENV, "").strip().lower() not in ("1", "true"):
            return
        counts = seed(conn)
        log.info("seeded synthetic data: %s", counts)
        path = env.get(SEED_PASSWORD_FILE_ENV)
        password = Path(path).read_text(encoding="utf-8").strip() if path else ""
        if password:
            seed_users(conn, password)
        else:
            log.warning("CRIBA_SEED=1 without %s: no users seeded", SEED_PASSWORD_FILE_ENV)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The runtime
# ---------------------------------------------------------------------------


@dataclass
class Runtime:
    clock: Clock
    channel: Channel
    summary: SummaryWriter
    scheduler: Scheduler
    scheduler_interval_s: float = 60.0
    voice: VoiceRuntime | None = None
    # Set by build_runtime right after construction: the context refers back to the runtime.
    api_context: ApiContext = field(init=False)

    def connect(self) -> sqlite3.Connection:
        return self.channel.new_connection()

    def write_summary(self, application_id: str, trigger: Trigger) -> None:
        conn = self.connect()
        try:
            self.summary.write(conn, application_id, trigger=trigger)
        finally:
            conn.close()

    def after_tool_call(self, tool: str, session_id: str, body: dict[str, Any]) -> None:
        """The tools router's after-call hook: the summary once Screening ends."""
        try:
            trigger = summary_trigger(tool, body)
            if trigger is None and tool == "record_answer" and _accepted(body):
                conn = self.connect()
                try:
                    trigger = "reopened" if reopened_by_answer(conn, session_id) else None
                finally:
                    conn.close()
            if trigger is not None:
                self.write_summary(session_id, trigger)
        except Exception:
            log.exception("summary after %s failed for %s", tool, session_id)

    def notify(self, application_id: str, event_kind: str) -> None:
        """``services.notify``: a Review that overturns a Knockout reopens Screening."""
        if event_kind == "review_overturned":
            in_background(
                lambda: self.write_summary(application_id, "reopened"),
                f"summary-reopened-{application_id}",
            )

    def reopened(self, application_id: str) -> None:
        """The router's ``on_reopened``: a chat message reopened an Abandoned Application."""
        in_background(
            lambda: self.write_summary(application_id, "reopened"),
            f"summary-reopened-{application_id}",
        )

    def submit_action(self, conn: sqlite3.Connection, application_id: str, action: str) -> None:
        """``services.submit_action``: called after the Approval committed (Q41).

        The scheduler holds the action past quiet hours or dispatches it, which may run the
        agent; that happens on its own thread and connection, so the Approve request does
        not wait for an agent run.
        """

        def submit() -> None:
            own = self.connect()
            try:
                self.scheduler.submit_action(own, application_id, action)
            finally:
                own.close()

        in_background(submit, f"submit-{action}-{application_id}")

    def run_due_jobs(self) -> int:
        return jobs_run(self.scheduler.run_due_jobs())


def voice_drift_cache(settings: VoiceSettings) -> AgentDriftCache | None:
    """The started agent drift cache (decisions 25, 39): ``agent_drift`` at once and every
    10 minutes, so R2 refuses voice sessions on drift or a failed check. None in sim and
    test, and when voice is off. The lambda looks ``agent_drift`` up when it runs."""
    if settings.environment in ("sim", "test") or not settings.enabled:
        return None
    cache = AgentDriftCache(lambda: agent_drift(settings))
    cache.start()
    return cache


def build_runtime(
    env: Mapping[str, str] | None = None,
    *,
    run_client: RunClientProtocol | None = None,
    summary_client: SummaryClient | None = None,
    clock: Clock | None = None,
    prepare_db: bool = True,
) -> Runtime:
    env = os.environ if env is None else env
    if prepare_db:
        prepare_database(env)
    settings = ApiSettings.from_env(env)
    shared: Clock = clock or (SimClock() if settings.is_sim else SystemClock())
    channel_settings = ChannelSettings.from_env(env)
    jev = jev_step_from_env(env, environment=channel_settings.environment)
    overrides: dict[str, Any] = {
        "settings": channel_settings,
        "jev": jev,
        # Read at request time, once `channel` exists: one Jev step and one pending-choice
        # path for typed and spoken turns (spoken-checks, log decision 44).
        "spoken_checks": SpokenChecker(
            pending=lambda conn, app_id: channel.router.pending_interactive(conn, app_id),
            jev=jev,
        ),
    }
    if run_client is not None:
        overrides["run_client"] = run_client
    channel = Channel.from_env(
        clock=shared,
        pending_choice=pending_choice,
        interview_offer=interview_offer,
        **overrides,
    )
    client = summary_client if summary_client is not None else summary_client_from_env(env)
    summary = SummaryWriter(client, shared, environment=channel_settings.environment)
    key_check = key_check_from_env(env, alarms=channel.router.alarms, clock=shared)
    scheduler = build_scheduler(
        channel, summary=summary, key_check=key_check.run_if_due if key_check else None
    )
    runtime = Runtime(
        clock=shared,
        channel=channel,
        summary=summary,
        scheduler=scheduler,
        scheduler_interval_s=float(env.get(SCHEDULER_INTERVAL_ENV, "60")),
        voice=voice_runtime_from_env(env),
    )
    if runtime.voice is not None:
        runtime.voice = replace(runtime.voice, drift=voice_drift_cache(runtime.voice.settings))
    runtime.api_context = ApiContext(
        settings=settings,
        clock=shared,
        services=Services(
            sender=ChannelCandidateSender(channel),
            booking=ToolsBookingService(),
            comparison=UsageComparison(),
            notify=runtime.notify,
            submit_action=runtime.submit_action,
        ),
        run_due_jobs=runtime.run_due_jobs,
    )
    channel.router.on_reopened = runtime.reopened
    return runtime
