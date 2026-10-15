"""src/scripts/select_tests.py: which tests `check.sh task` runs for a set of changed files.

Each test builds a small copy of the real layout in a temporary directory, so the mapping is
checked against what exists, not against this checkout.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[4] / "src" / "scripts" / "select_tests.py"
spec = importlib.util.spec_from_file_location("select_tests", SCRIPT)
assert spec is not None and spec.loader is not None
select_tests = importlib.util.module_from_spec(spec)
sys.modules["select_tests"] = select_tests
spec.loader.exec_module(select_tests)

LAYOUT = [
    "pytest.ini",
    "src/backend/pyproject.toml",
    "src/backend/uv.lock",
    "src/backend/criba/__init__.py",
    "src/backend/criba/app.py",
    "src/backend/criba/wiring.py",
    "src/backend/criba/summary.py",
    "src/backend/criba/orphan.py",
    "src/backend/criba/channel/sender.py",
    "src/backend/criba/channel/webchat.py",
    "src/backend/criba/newarea/thing.py",
    "src/backend/criba/db/repo.py",
    "src/backend/criba/db/migrations/0001_schema.sql",
    "src/backend/tests/conftest.py",
    "src/backend/tests/test_app.py",
    "src/backend/tests/test_wiring.py",
    "src/backend/tests/channel/conftest.py",
    "src/backend/tests/channel/helpers.py",
    "src/backend/tests/channel/test_sender.py",
    "src/backend/tests/channel/test_webchat.py",
    "src/backend/tests/db/test_repo.py",
    "src/backend/tests/summary/test_writer.py",
    "src/backend/tests/hermes/test_profiles.py",
    "src/backend/tests/api/test_sim.py",
    "src/tests/sim/driver.py",
    "src/tests/sim/test_driver.py",
    "src/tests/e2e/test_sim_stack.py",
    "src/hermes/profiles/criba-recruiter/config.yaml",
    "contracts/api.openapi.yaml",
    "docker-compose.yml",
    "docker-compose.sim.yml",
    "src/web/package.json",
    "src/web/package-lock.json",
    "src/web/vite.config.ts",
    "src/web/tsconfig.app.json",
    "src/web/eslint.config.js",
    "src/web/tests/setup.ts",
    "src/web/tests/e2e/login.spec.ts",
    "src/web/tests/unit/format.test.ts",
    "src/web/src/App.tsx",
    "src/web/src/App.test.tsx",
    "src/web/src/components/Badge.tsx",
    "src/web/src/styles/app.css",
    "README.md",
    "docs/notes.md",
]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    for rel in LAYOUT:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n")
    return tmp_path


def backend(root: Path, *changed: str):
    return select_tests.select(list(changed), root).backend


def web(root: Path, *changed: str):
    return select_tests.select(list(changed), root).web


# --- backend: a changed test file ---------------------------------------------------------------


@pytest.mark.parametrize(
    "changed",
    [
        "src/backend/tests/channel/test_sender.py",
        "src/tests/sim/test_driver.py",
        "src/backend/tests/test_wiring.py",
    ],
)
def test_changed_test_file_runs_that_file(root: Path, changed: str) -> None:
    sel = backend(root, changed)
    assert (sel.mode, sel.targets) == ("related", [changed])


def test_deleted_test_file_runs_its_directory(root: Path) -> None:
    sel = backend(root, "src/backend/tests/channel/test_gone.py")
    assert (sel.mode, sel.targets) == ("related", ["src/backend/tests/channel"])


def test_deleted_top_level_test_file_runs_everything(root: Path) -> None:
    sel = backend(root, "src/backend/tests/test_gone.py")
    assert sel.mode == "full"
    assert "was deleted" in sel.reason


def test_deleted_test_in_a_deleted_directory_runs_everything(root: Path) -> None:
    sel = backend(root, "src/backend/tests/gone/test_x.py")
    assert sel.mode == "full"


def test_helper_in_a_test_area_runs_the_area(root: Path) -> None:
    sel = backend(root, "src/backend/tests/channel/helpers.py")
    assert (sel.mode, sel.targets) == ("related", ["src/backend/tests/channel"])


def test_file_directly_under_tests_runs_everything(root: Path) -> None:
    assert backend(root, "src/backend/tests/__init__.py").mode == "full"


# --- backend: criba source ----------------------------------------------------------------------


def test_area_source_runs_the_area_tests(root: Path) -> None:
    sel = backend(
        root, "src/backend/criba/channel/sender.py", "src/backend/criba/channel/webchat.py"
    )
    assert (sel.mode, sel.targets) == ("related", ["src/backend/tests/channel"])


def test_module_source_runs_its_test_file(root: Path) -> None:
    sel = backend(root, "src/backend/criba/wiring.py")
    assert (sel.mode, sel.targets) == ("related", ["src/backend/tests/test_wiring.py"])
    assert backend(root, "src/backend/criba/app.py").targets == ["src/backend/tests/test_app.py"]


def test_module_source_runs_its_test_directory(root: Path) -> None:
    sel = backend(root, "src/backend/criba/summary.py")
    assert (sel.mode, sel.targets) == ("related", ["src/backend/tests/summary"])


def test_module_with_test_file_and_directory_runs_both(root: Path) -> None:
    (root / "src/backend/tests/test_summary.py").write_text("x\n")
    sel = backend(root, "src/backend/criba/summary.py")
    assert sel.targets == ["src/backend/tests/test_summary.py", "src/backend/tests/summary"]


@pytest.mark.parametrize(
    "changed",
    [
        "src/backend/criba/orphan.py",
        "src/backend/criba/__init__.py",
        "src/backend/criba/newarea/thing.py",
        "src/backend/criba/py.typed",
    ],
)
def test_source_that_maps_to_nothing_runs_everything(root: Path, changed: str) -> None:
    sel = backend(root, changed)
    assert sel.mode == "full"
    assert changed in sel.reason
    assert sel.targets == []


def test_unmapped_source_wins_over_mapped_ones(root: Path) -> None:
    sel = backend(root, "src/backend/criba/channel/sender.py", "src/backend/criba/orphan.py")
    assert sel.mode == "full"


def test_related_areas_are_combined_and_deduplicated(root: Path) -> None:
    sel = backend(
        root,
        "src/backend/criba/channel/sender.py",
        "src/backend/tests/channel/test_sender.py",
        "src/backend/criba/db/repo.py",
        "src/backend/criba/wiring.py",
    )
    assert sel.mode == "related"
    assert sel.targets == [
        "src/backend/tests/channel",
        "src/backend/tests/db",
        "src/backend/tests/test_wiring.py",
    ]
    assert sel.reason == "related (" + ", ".join(sel.targets) + ")"


# --- backend: the sim tests and the files the tests read ----------------------------------------


def test_sim_change_runs_the_sim_tests(root: Path) -> None:
    sel = backend(root, "src/tests/sim/driver.py")
    assert (sel.mode, sel.targets) == ("related", ["src/tests/sim"])


def test_sim_helper_and_test_collapse_into_the_directory(root: Path) -> None:
    sel = backend(root, "src/tests/sim/driver.py", "src/tests/sim/test_driver.py")
    assert sel.targets == ["src/tests/sim"]


def test_e2e_test_change_runs_that_file(root: Path) -> None:
    sel = backend(root, "src/tests/e2e/test_sim_stack.py")
    assert sel.targets == ["src/tests/e2e/test_sim_stack.py"]


@pytest.mark.parametrize(
    "changed",
    [
        "src/backend/tests/conftest.py",
        "src/backend/tests/channel/conftest.py",
        "src/tests/sim/conftest.py",
        "conftest.py",
        "pytest.ini",
        "src/backend/pyproject.toml",
        "src/backend/uv.lock",
        "src/backend/criba/db/migrations/0001_schema.sql",
        "src/backend/criba/db/migrations/0007_new.sql",
        "contracts/api.openapi.yaml",
    ],
)
def test_shared_config_runs_everything(root: Path, changed: str) -> None:
    sel = backend(root, changed, "src/backend/criba/channel/sender.py")
    assert sel.mode == "full"
    assert changed in sel.reason
    assert sel.targets == []


def test_other_backend_file_runs_everything_but_docs_do_not(root: Path) -> None:
    assert backend(root, "src/backend/.python-version").mode == "full"
    assert backend(root, "src/backend/README.md").mode == "skip"


def test_hermes_and_compose_files_run_the_hermes_tests(root: Path) -> None:
    assert backend(root, "src/hermes/profiles/criba-recruiter/config.yaml").targets == [
        "src/backend/tests/hermes"
    ]
    assert backend(root, "docker-compose.yml").targets == ["src/backend/tests/hermes"]
    assert backend(root, "docker-compose.sim.yml").targets == [
        "src/backend/tests/hermes",
        "src/backend/tests/api/test_sim.py",
    ]


def test_no_backend_change_skips_backend(root: Path) -> None:
    sel = backend(root, "README.md", "docs/notes.md", "src/web/src/App.tsx")
    assert (sel.mode, sel.targets) == ("skip", [])
    assert "no backend-relevant change" in sel.reason


def test_no_change_at_all_skips_both(root: Path) -> None:
    result = select_tests.select([], root)
    assert (result.backend.mode, result.web.mode) == ("skip", "skip")


# --- web ----------------------------------------------------------------------------------------


def test_web_source_and_test_run_related(root: Path) -> None:
    sel = web(root, "src/web/src/components/Badge.tsx", "src/web/src/App.test.tsx")
    assert sel.mode == "related"
    assert sel.targets == ["src/App.test.tsx", "src/components/Badge.tsx"]
    assert sel.reason == "related (src/App.test.tsx, src/components/Badge.tsx)"


def test_web_unit_test_and_style_run_related(root: Path) -> None:
    sel = web(root, "src/web/tests/unit/format.test.ts", "src/web/src/styles/app.css")
    assert (sel.mode, sel.targets) == (
        "related",
        ["src/styles/app.css", "tests/unit/format.test.ts"],
    )


@pytest.mark.parametrize(
    "changed",
    [
        "src/web/package.json",
        "src/web/package-lock.json",
        "src/web/vite.config.ts",
        "src/web/tsconfig.app.json",
        "src/web/tests/setup.ts",
    ],
)
def test_web_config_runs_the_full_suite(root: Path, changed: str) -> None:
    sel = web(root, changed, "src/web/src/App.tsx")
    assert sel.mode == "full"
    assert sel.targets == []
    assert changed.removeprefix("src/web/") in sel.reason


def test_vitest_config_runs_the_full_suite(root: Path) -> None:
    assert web(root, "src/web/vitest.config.ts").mode == "full"
    assert web(root, "src/web/vitest.workspace.ts").mode == "full"


def test_web_deleted_file_is_not_passed_to_vitest(root: Path) -> None:
    sel = web(root, "src/web/src/Gone.tsx", "src/web/src/App.tsx")
    assert sel.targets == ["src/App.tsx"]
    assert web(root, "src/web/src/Gone.tsx").mode == "skip"


@pytest.mark.parametrize(
    "changed",
    [
        "src/web/tests/e2e/login.spec.ts",
        "src/web/eslint.config.js",
        "src/web/README.md",
        "src/web/node_modules/x/index.js",
        "src/web/coverage/cobertura-coverage.xml",
        "src/backend/criba/wiring.py",
    ],
)
def test_files_outside_the_vitest_graph_skip_web(root: Path, changed: str) -> None:
    assert web(root, changed).mode == "skip"


def test_web_change_does_not_select_backend_tests(root: Path) -> None:
    result = select_tests.select(["src/web/src/App.tsx"], root)
    assert (result.backend.mode, result.web.mode) == ("skip", "related")


# --- command line -------------------------------------------------------------------------------


def run_cli(root: Path, *changed: str) -> dict[str, list[str]]:
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)],
        input="\0".join(changed).encode(),
        capture_output=True,
        check=True,
    ).stdout.decode()
    lines: dict[str, list[str]] = {}
    for line in out.splitlines():
        key, _, value = line.partition("\t")
        lines.setdefault(key, []).append(value)
    return lines


def test_cli_prints_mode_reason_and_targets(root: Path) -> None:
    out = run_cli(
        root, "src/backend/criba/channel/sender.py", "src/web/src/App.tsx", "src/web/src/Gone.tsx"
    )
    assert out["backend.mode"] == ["related"]
    assert out["backend.reason"] == ["related (src/backend/tests/channel)"]
    assert out["backend.target"] == ["src/backend/tests/channel"]
    assert out["web.mode"] == ["related"]
    assert out["web.file"] == ["src/App.tsx"]


def test_cli_with_no_changes_skips_both_sides(root: Path) -> None:
    out = run_cli(root)
    assert out["backend.mode"] == ["skip"]
    assert out["web.mode"] == ["skip"]
    assert "backend.target" not in out
    assert "web.file" not in out


def test_cli_keeps_unusual_file_names_intact(root: Path) -> None:
    name = "src/web/src/odd name (1).tsx"
    (root / name).write_text("x\n")
    assert run_cli(root, name)["web.file"] == ["src/odd name (1).tsx"]
