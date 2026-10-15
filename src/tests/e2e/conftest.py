"""Put ``src/tests`` on ``sys.path`` so the e2e tests import ``sim.*`` like the sim tests do.

Tests marked ``sim_stack`` need a running sim stack and are collected only when
``CRIBA_E2E_BASE_URL`` is set; tests marked ``compose`` also need its Compose project name in
``COMPOSE_PROJECT_NAME`` (they reach a container with ``docker compose exec``). Without them the
tests are deselected, not skipped, and the run reports them as deselected.
"""

import os
import sys
from pathlib import Path

import pytest

_TESTS_ROOT = str(Path(__file__).resolve().parent.parent)
if _TESTS_ROOT not in sys.path:
    sys.path.insert(0, _TESTS_ROOT)

STACK_MARK = "sim_stack"
COMPOSE_MARK = "compose"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{STACK_MARK}: needs a running sim stack; collected only with CRIBA_E2E_BASE_URL",
    )
    config.addinivalue_line(
        "markers",
        f"{COMPOSE_MARK}: also needs the stack's Compose project; collected only with "
        "COMPOSE_PROJECT_NAME",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    has_stack = bool(os.environ.get("CRIBA_E2E_BASE_URL"))
    has_compose = has_stack and bool(os.environ.get("COMPOSE_PROJECT_NAME"))
    dropped = [
        item
        for item in items
        if (item.get_closest_marker(STACK_MARK) and not has_stack)
        or (item.get_closest_marker(COMPOSE_MARK) and not has_compose)
    ]
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = [item for item in items if item not in dropped]
