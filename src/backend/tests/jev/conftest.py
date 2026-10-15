"""Fixtures for the Jev router tests: a fake Jev (no network, no model) behind the real port,
and a channel wired with it over a fake Hermes."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from criba.channel.config import ChannelSettings
from criba.channel.service import Channel
from criba.channel.webchat import chat_router
from criba.db import connect, migrate
from criba.db.seed import seed
from criba.jev import JevFailed, JevInput, JevResult, Reading
from criba.jev.turn import JevStep
from criba.screening.clock import FakeClock
from criba.tools import pending_choice

from ..channel.conftest import NOW, FakeHermes, make_run_client

CALM = {"frustrated": 0.02, "confused": 0.02, "wants_human": 0.01, "abusive": 0.01}


def reading(
    lang: str = "es",
    confidence: float = 1.0,
    switch: float = 0.02,
    unreadable: float = 0.02,
    *,
    asks: str = "none",
    asks_confidence: float = 1.0,
    **signals: float,
) -> Reading:
    return Reading(
        lang,
        confidence,
        switch,
        {**CALM, **signals},
        asks=asks,
        asks_confidence=asks_confidence,
        unreadable=unreadable,
    )


class FakeJev:
    """A Jev that answers from a function of the request, or fails on demand."""

    model = "typesafe/jev-1.13"

    def __init__(self) -> None:
        self.requests: list[JevInput] = []
        self.answer: Callable[[JevInput], Reading] = lambda request: reading()
        self.failure: JevFailed | None = None
        self.cost: float | None = 3e-05

    def read(self, request: JevInput) -> JevResult:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        return JevResult(
            reading=self.answer(request),
            model="typesafe/jev-1.13-20260917",
            request_id="gen-dec-test",
            input_tokens=400,
            output_tokens=60,
            cost_usd=self.cost,
            latency_ms=310,
        )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "criba.db"
    db = connect(path)
    migrate(db)
    seed(db, slots_from=date(2026, 10, 12), days=7, now=NOW)
    db.close()
    return path


@pytest.fixture
def conn(db_path: Path) -> Iterator:
    connection = connect(db_path)
    yield connection
    connection.close()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
def hermes() -> FakeHermes:
    return FakeHermes()


@pytest.fixture
def jev() -> FakeJev:
    return FakeJev()


@pytest.fixture
def channel(db_path: Path, clock: FakeClock, hermes: FakeHermes, jev: FakeJev) -> Channel:
    return Channel(
        connect=lambda: connect(db_path),
        run_client=make_run_client(hermes),
        clock=clock,
        settings=ChannelSettings(db_path=str(db_path), environment="test"),
        pending_choice=pending_choice,
        interview_offer=lambda conn, application_id, now: {},
        jev=JevStep(jev, environment="test"),
    )


@pytest.fixture
def client(channel: Channel) -> TestClient:
    app = FastAPI()
    app.include_router(chat_router, prefix="/api/chat")
    app.state.criba_channel = channel
    return TestClient(app)
