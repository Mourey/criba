"""Make `live` and `sim` importable, and keep the live tests out of every ordinary run.

A test marked `live` drives real conversations on a real model and spends money, so it is
collected only when CRIBA_LIVE is 1 (the same pattern as `live_model` in `sim/conftest.py`).
Without it the test is deselected and the run reports "N deselected". `src/tests` must be on
`sys.path` whatever the pytest import mode or working directory.
"""

import os
import sys
from pathlib import Path

import pytest

_TESTS_ROOT = str(Path(__file__).resolve().parent.parent)
if _TESTS_ROOT not in sys.path:
    sys.path.insert(0, _TESTS_ROOT)

LIVE_MARK = "live"


def live_enabled() -> bool:
    return os.environ.get("CRIBA_LIVE") == "1"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{LIVE_MARK}: drives real conversations on a real model and spends money; collected "
        "only with CRIBA_LIVE=1 and never in CI",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if live_enabled():
        return
    live = [item for item in items if item.get_closest_marker(LIVE_MARK)]
    if live:
        config.hook.pytest_deselected(items=live)
        items[:] = [item for item in items if item not in live]
