"""The live smoke run as a pytest test: real conversations on the real model. Opt in, spends money.

Deselected unless CRIBA_LIVE=1 (`conftest.py`), so CI and `check.sh` never reach a model. Against
a real stack (no sim overlay; `scripts/live_smoke.sh` brings one up)::

    CRIBA_LIVE=1 CRIBA_LIVE_BASE_URL=http://localhost:18160 \\
    CRIBA_E2E_SEED_PASSWORD_FILE=secrets/criba_seed_password.txt \\
    CRIBA_REAL_SPEND_CAP_USD=3 CRIBA_REAL_SPEND_LEDGER=/tmp/criba-real-spend.json \\
        uv run --project src/backend pytest src/tests/live/test_live_smoke.py -q

The flows run once for the module; each test reads one flow's result, so a failure names the
flow. The run needs the same cap as `python -m live.smoke` and refuses without it.
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from live import smoke
from live.flows import FLOWS

pytestmark = pytest.mark.live

BASE_URL = os.environ.get("CRIBA_LIVE_BASE_URL", "")


@pytest.fixture(scope="module")
def results(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    if not BASE_URL:
        pytest.fail("set CRIBA_LIVE_BASE_URL to the real stack, for example http://localhost:18160")
    dump = tmp_path_factory.mktemp("live") / "results.json"
    report = dump.with_suffix(".md")
    code = smoke.main(["--base-url", BASE_URL, "--json", str(dump), "--out", str(report)])
    if not dump.exists():
        pytest.fail(f"the live smoke run produced no results (exit code {code}); see its output")
    data = json.loads(dump.read_text(encoding="utf-8"))
    return {r["id"]: r for r in data["results"]}


@pytest.mark.parametrize("flow_id", [f.id for f in FLOWS])
def test_flow(results: dict[str, dict[str, Any]], flow_id: str) -> None:
    result = results[flow_id]
    problems = [f"{c['name']}: {c['detail']}" for c in result["checks"] if not c["ok"]]
    status = "not run" if result["not_run"] else "error" if result["error"] else None
    assert status is None, f"{flow_id} {status}: {result['not_run'] or result['error']}"
    assert not problems, f"{flow_id} failed:\n  - " + "\n  - ".join(problems)
    assert result["checks"], f"{flow_id} made no checks"
