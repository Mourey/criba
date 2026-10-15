from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from criba.db import connect, migrate
from criba.db.seed import seed

NOW = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "criba.db"


@pytest.fixture
def conn(db_path: Path) -> Iterator:
    connection = connect(db_path)
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def seeded(conn):
    seed(conn, slots_from=date(2026, 10, 12), days=7, now=NOW)
    return conn
