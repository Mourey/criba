"""Make `sim` importable whatever the pytest import mode or working directory.

`src/tests/sim` lives outside the backend package, and the backend's pytest config
(`--import-mode=importlib`) only applies when pytest is run from `src/backend`. Putting
`src/tests` on `sys.path` lets tests and the stub server use absolute `sim.*` imports in
both cases.
"""

import os
import sys
from pathlib import Path

import pytest

_TESTS_ROOT = str(Path(__file__).resolve().parent.parent)
if _TESTS_ROOT not in sys.path:
    sys.path.insert(0, _TESTS_ROOT)


# ---- tests that call a real model ------------------------------------------------------------
# A test marked `live_model` spends money, so it is collected only when CRIBA_LIVE_MODEL is 1
# (spec 006b: the phrase check W3-3 runs, never CI). Without it the test is deselected and the
# run reports "1 deselected".

LIVE_MODEL_MARK = "live_model"


def live_model_enabled() -> bool:
    return os.environ.get("CRIBA_LIVE_MODEL") == "1"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{LIVE_MODEL_MARK}: calls a real model and spends money; collected only with "
        "CRIBA_LIVE_MODEL=1 and never in CI",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if live_model_enabled():
        return
    live = [item for item in items if item.get_closest_marker(LIVE_MODEL_MARK)]
    if live:
        config.hook.pytest_deselected(items=live)
        items[:] = [item for item in items if item not in live]
