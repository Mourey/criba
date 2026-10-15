"""Fixtures for the Hermes-side tests.

Two kinds of test live here:

* Host tests (no marker): the criba-tools plugin and the profile files, checked from this
  Python process. No Docker needed.
* ``@pytest.mark.hermes`` tests: run against a live container started with
  ``docker compose -p <project> up -d --build hermes``. The Compose project is read from
  ``COMPOSE_PROJECT_NAME`` (Compose's own variable) and defaults to ``criba``. None of them
  makes a model call.

Without a running container the ``hermes`` tests skip, unless ``CRIBA_REQUIRE_HERMES=1`` is
set (the gate does), in which case they fail.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

# Importing the plugin from the source tree must not leave __pycache__ next to it: that
# directory is copied into the Docker image.
sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[4]
HERMES_SRC = REPO_ROOT / "src" / "hermes"
SKILLS_SRC = REPO_ROOT / "src" / "skills"
CONTRACT_PATH = REPO_ROOT / "contracts" / "tools.json"
PLUGIN_DIR = HERMES_SRC / "plugins" / "criba_tools"
PROFILES = (
    "criba-recruiter",
    "criba-recruiter-sim",
    "criba-recruiter-voice",
    "criba-recruiter-voice-sim",
)
# The one skill each profile lists: voice calls have their own skill, written for speech
# (ADR 0059).
PROFILE_SKILLS = {
    "criba-recruiter": "criba/screener",
    "criba-recruiter-sim": "criba/screener",
    "criba-recruiter-voice": "criba/voice-screener",
    "criba-recruiter-voice-sim": "criba/voice-screener",
}
HERMES_PYTHON = "/opt/hermes/.venv/bin/python"
HERMES_DATA = "/opt/data"

CONTAINER_SCRIPTS = Path(__file__).resolve().parent / "container"


# --- the plugin, imported from the source tree ---------------------------------------------


def _load_package(name: str, directory: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        name, directory / "__init__.py", submodule_search_locations=[str(directory)]
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def plugin() -> ModuleType:
    """The ``criba_tools`` package, loaded the way Hermes loads a directory plugin."""
    return _load_package("criba_tools_under_test", PLUGIN_DIR)


@pytest.fixture(scope="session")
def contract_module(plugin: ModuleType) -> ModuleType:
    return sys.modules[f"{plugin.__name__}.contract"]


@pytest.fixture(scope="session")
def client_module(plugin: ModuleType) -> ModuleType:
    return sys.modules[f"{plugin.__name__}.client"]


@pytest.fixture(scope="session")
def contract() -> dict[str, Any]:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


# --- a recording backend ------------------------------------------------------------------


@dataclass
class RecordedRequest:
    method: str
    path: str
    headers: dict[str, str]
    body: Any


@dataclass
class Reply:
    status: int = 200
    body: bytes | dict[str, Any] = field(default_factory=lambda: {"ok": True, "value": {}})
    delay: float = 0.0


class RecordingBackend:
    """A stand-in for ``POST /internal/tools/{name}`` that records what it receives."""

    def __init__(self) -> None:
        self.requests: list[RecordedRequest] = []
        self.reply: Callable[[RecordedRequest], Reply] = lambda _request: Reply()
        backend = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length)
                try:
                    body: Any = json.loads(raw)
                except ValueError:
                    body = raw
                request = RecordedRequest(
                    "POST", self.path, {k.lower(): v for k, v in self.headers.items()}, body
                )
                backend.requests.append(request)
                reply = backend.reply(request)
                if reply.delay:
                    time.sleep(reply.delay)
                payload = (
                    reply.body
                    if isinstance(reply.body, bytes)
                    else json.dumps(reply.body).encode("utf-8")
                )
                self.send_response(reply.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> Iterator[RecordingBackend]:
    """A recording backend, wired into the plugin through its environment variables."""
    server = RecordingBackend()
    server.start()
    monkeypatch.setenv("CRIBA_BACKEND_URL", server.url)
    monkeypatch.setenv("CRIBA_TOOLS_SECRET", "unit-test-secret")
    monkeypatch.delenv("CRIBA_TOOLS_SECRET_FILE", raising=False)
    monkeypatch.delenv("CRIBA_TOOLS_TIMEOUT", raising=False)
    try:
        yield server
    finally:
        server.stop()


# --- the live container -------------------------------------------------------------------


class HermesContainer:
    """Runs commands in the ``hermes`` service of the Compose project under test."""

    def __init__(self, project: str) -> None:
        self.project = project

    def compose(self, *args: str, stdin: str | None = None, timeout: int = 120) -> str:
        result = subprocess.run(
            ["docker", "compose", "-p", self.project, *args],
            cwd=REPO_ROOT,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"docker compose {' '.join(args)} failed ({result.returncode}):\n"
                f"{result.stdout}\n{result.stderr}"
            )
        return result.stdout

    def exec(
        self,
        *command: str,
        user: str | None = None,
        env: dict[str, str] | None = None,
        stdin: str | None = None,
        workdir: str | None = None,
        timeout: int = 120,
    ) -> str:
        args = ["exec", "-T"]
        if user:
            args += ["-u", user]
        for key, value in (env or {}).items():
            args += ["-e", f"{key}={value}"]
        if workdir:
            args += ["-w", workdir]
        args += ["hermes", *command]
        return self.compose(*args, stdin=stdin, timeout=timeout)

    def read_file(self, path: str) -> str:
        return self.exec("cat", path)

    def python_script(
        self, script: str, *script_args: str, profile: str | None = None, timeout: int = 180
    ) -> dict[str, Any]:
        """Run a script from tests/hermes/container/ under the image's Python and parse its
        last stdout line as JSON. ``profile=None`` is the root profile."""
        home = HERMES_DATA if profile is None else f"{HERMES_DATA}/profiles/{profile}"
        source = (CONTAINER_SCRIPTS / script).read_text(encoding="utf-8")
        out = self.exec(
            HERMES_PYTHON,
            "-",
            *script_args,
            user="hermes",
            env={"HERMES_HOME": home},
            workdir="/opt/hermes",
            stdin=source,
            timeout=timeout,
        )
        lines = [line for line in out.splitlines() if line.strip()]
        return json.loads(lines[-1])

    def http(
        self, method: str, path: str, *, key: str | None = None, body: Any = None
    ) -> tuple[int, Any]:
        """One HTTP call to the gateway from inside the container (nothing is published to the
        host). Returns ``(status, parsed body)``."""
        program = (
            "import json, sys, urllib.request, urllib.error\n"
            "a = json.loads(sys.argv[1])\n"
            "req = urllib.request.Request('http://127.0.0.1:8642' + a['path'],\n"
            "    method=a['method'],\n"
            "    data=json.dumps(a['body']).encode() if a['body'] is not None else None)\n"
            "if a['key']: req.add_header('Authorization', 'Bearer ' + a['key'])\n"
            "req.add_header('Content-Type', 'application/json')\n"
            "try:\n"
            "    r = urllib.request.urlopen(req, timeout=30); status, raw = r.status, r.read()\n"
            "except urllib.error.HTTPError as e:\n"
            "    status, raw = e.code, e.read()\n"
            "try: parsed = json.loads(raw)\n"
            "except ValueError: parsed = raw.decode('utf-8', 'replace')\n"
            "print(json.dumps({'status': status, 'body': parsed}))\n"
        )
        arguments = json.dumps({"method": method, "path": path, "key": key, "body": body})
        out = self.exec("python3", "-c", program, arguments)
        parsed = json.loads(out.strip().splitlines()[-1])
        return parsed["status"], parsed["body"]

    def secret(self, name: str) -> str:
        return self.read_file(f"/run/secrets/{name}").strip()


def _require(message: str) -> None:
    if os.environ.get("CRIBA_REQUIRE_HERMES") == "1":
        pytest.fail(message, pytrace=False)
    pytest.skip(message)


@pytest.fixture(scope="session")
def hermes_container() -> HermesContainer:
    project = os.environ.get("COMPOSE_PROJECT_NAME", "criba")
    container = HermesContainer(project)
    try:
        container_id = container.compose("ps", "-q", "hermes").strip()
    except (AssertionError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        _require(f"docker compose is not usable: {exc}")
        raise
    if not container_id:
        _require(
            f"no hermes container in Compose project {project!r}; "
            f"run: docker compose -p {project} up -d --build hermes"
        )
    deadline = time.monotonic() + 180
    status = ""
    while time.monotonic() < deadline:
        status = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Health.Status}}", container_id],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        if status == "healthy":
            return container
        time.sleep(3)
    pytest.fail(f"hermes container is not healthy after 180 s (status: {status!r})", pytrace=False)
    return container  # unreachable; keeps the type checker content


@pytest.fixture(scope="session")
def introspection(hermes_container: HermesContainer) -> dict[str | None, dict[str, Any]]:
    """What each profile's agent would see, computed once (it takes several seconds each)."""
    return {
        None: hermes_container.python_script("introspect.py"),
        **{p: hermes_container.python_script("introspect.py", profile=p) for p in PROFILES},
    }
