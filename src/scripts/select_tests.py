#!/usr/bin/env python3
"""Choose which tests `check.sh task` runs for a set of changed files (CONSTRAINTS.md, Placement).

Usage: select_tests.py [--root DIR] < NUL-separated changed paths (relative to the repo root)

Prints tab-separated `key<TAB>value` lines that check.sh reads:

    backend.mode    related | full | skip
    backend.reason  one line saying what was selected and why
    backend.target  one per line, a pytest path (only when mode is related)
    web.mode        related | full | skip
    web.reason      as above
    web.file        one per line, a path relative to src/web (only when mode is related)

Rules, backend (pytest, repo root):
  * conftest.py anywhere, pytest.ini, src/backend/pyproject.toml, src/backend/uv.lock,
    src/backend/criba/db/migrations/**, contracts/** (many tests load the frozen contracts)
    -> full suite;
  * src/backend/tests/**/test_*.py, src/tests/**/test_*.py -> that file;
  * another file under src/backend/tests/<area>/ or src/tests/<area>/ -> that directory;
  * src/backend/criba/<area>/** -> src/backend/tests/<area>/ if it exists;
  * src/backend/criba/<module>.py -> src/backend/tests/test_<module>.py and
    src/backend/tests/<module>/, whichever exist;
  * anything else under src/backend/criba/ that maps to nothing -> full suite, never zero tests;
  * src/hermes/**, src/skills/**, src/docker/hermes/**, docker-compose*.yml
    -> src/backend/tests/hermes (and tests/api/test_sim.py for docker-compose.sim.yml);
  * no backend-relevant change -> skip.
Rules, web (vitest, run from src/web):
  * package.json, package-lock.json, vite*.config.*, vitest*, tsconfig*, test setup files
    -> full suite;
  * any other changed file under src/web/ that still exists -> `vitest related` on those files
    (Markdown, Playwright e2e and eslint config are not part of the vitest graph and are skipped);
  * no web change -> skip.
A changed path that names a deleted test falls back to its nearest existing directory, or the
full suite when none exists.
"""

from __future__ import annotations

import argparse
import fnmatch
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

BACKEND_TESTS = "src/backend/tests"
BACKEND_SRC = "src/backend/criba"
SIM_TESTS = "src/tests"

BACKEND_FULL_FILES = {
    "pytest.ini",
    "src/backend/pyproject.toml",
    "src/backend/uv.lock",
}
BACKEND_FULL_PREFIXES = (f"{BACKEND_SRC}/db/migrations/", "contracts/")
HERMES_PREFIXES = ("src/hermes/", "src/skills/", "src/docker/hermes/")

WEB = "src/web"
WEB_FULL_GLOBS = (
    "package.json",
    "package-lock.json",
    "vite*.config.*",
    "vitest*",
    "tsconfig*",
    "setup.*",
    "setup-*",
    "*.setup.*",
)
WEB_IGNORED_GLOBS = ("*.md", "playwright.config.*", "eslint.config.*")


@dataclass
class Selection:
    mode: str = "skip"
    reason: str = ""
    targets: list[str] = field(default_factory=list)


@dataclass
class Result:
    backend: Selection
    web: Selection


def _clean(path: str) -> str:
    return PurePosixPath(path.strip()).as_posix()


def _is_test_file(rel: str) -> bool:
    name = PurePosixPath(rel).name
    return name.startswith("test_") and name.endswith(".py")


def _nearest_existing_dir(root: Path, rel: str, stop: str) -> str | None:
    """Closest existing ancestor of `rel` that lies strictly below `stop`."""
    for parent in PurePosixPath(rel).parents:
        if parent.as_posix() == stop:
            return None
        if (root / parent).is_dir():
            return parent.as_posix()
    return None


class _BackendSelector:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.full_reasons: list[str] = []
        self.targets: list[str] = []
        self.relevant = False

    def full(self, why: str) -> None:
        self.relevant = True
        if why not in self.full_reasons:
            self.full_reasons.append(why)

    def add(self, target: str) -> None:
        self.relevant = True
        if target not in self.targets:
            self.targets.append(target)

    def exists(self, rel: str) -> bool:
        return (self.root / rel).exists()

    def tests_tree(self, rel: str, base: str) -> None:
        """A path under src/backend/tests or src/tests."""
        if _is_test_file(rel):
            if self.exists(rel):
                self.add(rel)
                return
            parent = _nearest_existing_dir(self.root, rel, base)
            if parent:
                self.add(parent)
            else:
                self.full(f"{rel} was deleted")
            return
        parts = PurePosixPath(rel).relative_to(base).parts
        if len(parts) == 1:
            self.full(f"{rel} is shared by every test")
            return
        area = f"{base}/{parts[0]}"
        if self.exists(area):
            self.add(area)
        else:
            self.full(f"{rel} maps to no test directory")

    def criba_source(self, rel: str) -> None:
        parts = PurePosixPath(rel).relative_to(BACKEND_SRC).parts
        if len(parts) > 1:
            area = f"{BACKEND_TESTS}/{parts[0]}"
            if self.exists(area):
                self.add(area)
            else:
                self.full(f"{rel} maps to no tests")
            return
        module = PurePosixPath(parts[0]).stem
        mapped = [
            t
            for t in (f"{BACKEND_TESTS}/test_{module}.py", f"{BACKEND_TESTS}/{module}")
            if self.exists(t)
        ]
        if mapped and PurePosixPath(parts[0]).suffix == ".py":
            for target in mapped:
                self.add(target)
        else:
            self.full(f"{rel} maps to no tests")

    def visit(self, rel: str) -> None:
        name = PurePosixPath(rel).name
        if (
            name == "conftest.py"
            or rel in BACKEND_FULL_FILES
            or rel.startswith(BACKEND_FULL_PREFIXES)
        ):
            self.full(f"{rel} changed")
        elif rel.startswith(f"{BACKEND_TESTS}/"):
            self.tests_tree(rel, BACKEND_TESTS)
        elif rel.startswith(f"{SIM_TESTS}/"):
            self.tests_tree(rel, SIM_TESTS)
        elif rel.startswith(f"{BACKEND_SRC}/"):
            self.criba_source(rel)
        elif rel.startswith(HERMES_PREFIXES):
            self.add(f"{BACKEND_TESTS}/hermes")
        elif fnmatch.fnmatch(rel, "docker-compose*.yml"):
            self.add(f"{BACKEND_TESTS}/hermes")
            if rel.endswith(".sim.yml"):
                self.add(f"{BACKEND_TESTS}/api/test_sim.py")
        elif rel.startswith("src/backend/") and not rel.endswith(".md"):
            self.full(f"{rel} changed")

    def result(self) -> Selection:
        if self.full_reasons:
            return Selection("full", f"full ({', '.join(self.full_reasons)})")
        if not self.relevant:
            return Selection("skip", "skipped (no backend-relevant change)")
        targets = [
            t
            for t in self.targets
            if not any(t != d and t.startswith(d + "/") for d in self.targets)
        ]
        return Selection("related", f"related ({', '.join(targets)})", targets)


def select_backend(files: list[str], root: Path) -> Selection:
    selector = _BackendSelector(root)
    for rel in files:
        selector.visit(rel)
    return selector.result()


def select_web(files: list[str], root: Path) -> Selection:
    prefix = f"{WEB}/"
    changed = [f[len(prefix) :] for f in files if f.startswith(prefix)]
    changed = [
        f
        for f in changed
        if not f.startswith(("node_modules/", "coverage/", "dist/", "tests/e2e/"))
        and not any(fnmatch.fnmatch(PurePosixPath(f).name, g) for g in WEB_IGNORED_GLOBS)
    ]
    full = [
        f for f in changed if any(fnmatch.fnmatch(PurePosixPath(f).name, g) for g in WEB_FULL_GLOBS)
    ]
    if full:
        return Selection("full", f"full ({', '.join(full)} changed)")
    existing = [f for f in changed if (root / WEB / f).is_file()]
    if not existing:
        why = "only deleted files" if changed else "no web change"
        return Selection("skip", f"skipped ({why})")
    return Selection("related", f"related ({', '.join(existing)})", existing)


def select(files: list[str], root: Path) -> Result:
    cleaned = sorted({_clean(f) for f in files if f.strip()})
    return Result(select_backend(cleaned, root), select_web(cleaned, root))


def render(result: Result) -> str:
    lines = []
    for key, sel in (("backend", result.backend), ("web", result.web)):
        lines.append(f"{key}.mode\t{sel.mode}")
        lines.append(f"{key}.reason\t{sel.reason}")
        item = "target" if key == "backend" else "file"
        lines.extend(f"{key}.{item}\t{t}" for t in sel.targets)
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    raw = sys.stdin.buffer.read().decode("utf-8")
    files = [f for f in raw.split("\0") if f]
    sys.stdout.write(render(select(files, args.root)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
