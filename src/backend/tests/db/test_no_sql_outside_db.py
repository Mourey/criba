"""SQL lives in ``criba/db/repo``; this baseline may only go down.

Every ``.execute(``, ``.executemany(`` and ``.executescript(`` call in ``criba/`` outside
``criba/db/`` is counted per file with ``ast`` (strings and comments do not count) and
compared with ``BASELINE``:

- a file with more calls than its entry (or any call in a file not listed) fails: put the new
  query in a function in the matching ``criba/db/repo`` module and call ``repo.<name>(conn, ...)``
  instead of raising the baseline;
- a file with fewer calls than its entry fails too, so the progress is locked in: lower its
  entry (or remove it at zero) in the same change;
- an entry for a file that no longer exists fails.

The baseline is empty: every statement lives in ``criba/db``.
"""

from __future__ import annotations

import ast
from pathlib import Path

CRIBA = Path(__file__).resolve().parents[2] / "criba"
SQL_CALLS = frozenset({"execute", "executemany", "executescript"})

# Raw SQL calls per file outside criba/db/, relative to criba/. Only ever lower these.
BASELINE: dict[str, int] = {}

ADD_TO_REPO = (
    "New SQL goes in criba/db/repo: add a function to the matching repo module, re-export it "
    "from repo/__init__.py and call repo.<name>(conn, ...). Do not raise the baseline."
)


def sql_calls(source: str) -> int:
    """How many calls in ``source`` are ``<anything>.execute/executemany/executescript(...)``."""
    return sum(
        1
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in SQL_CALLS
    )


def counts() -> dict[str, int]:
    """Calls per file under ``criba/`` outside ``criba/db/``, files with none left out."""
    out: dict[str, int] = {}
    for path in sorted(CRIBA.rglob("*.py")):
        rel = path.relative_to(CRIBA).as_posix()
        if rel.startswith("db/"):
            continue
        n = sql_calls(path.read_text(encoding="utf-8"))
        if n:
            out[rel] = n
    return out


def test_no_file_outside_db_gains_raw_sql() -> None:
    grown = {
        rel: f"{n} calls, baseline {BASELINE.get(rel, 0)}"
        for rel, n in counts().items()
        if n > BASELINE.get(rel, 0)
    }
    assert not grown, f"Raw SQL added outside criba/db/: {grown}. {ADD_TO_REPO}"


def test_the_baseline_is_lowered_when_sql_moves_out() -> None:
    current = counts()
    shrunk = {
        rel: f"{current.get(rel, 0)} calls, baseline {n}"
        for rel, n in BASELINE.items()
        if (CRIBA / rel).exists() and current.get(rel, 0) < n
    }
    assert not shrunk, (
        f"Fewer raw SQL calls than the baseline: {shrunk}. Good: lower BASELINE in "
        "tests/db/test_no_sql_outside_db.py to the new count (remove the entry at zero) in the "
        "same change, so the progress is kept."
    )


def test_every_baseline_entry_names_an_existing_file() -> None:
    gone = sorted(rel for rel in BASELINE if not (CRIBA / rel).is_file())
    assert not gone, f"BASELINE lists files that no longer exist: {gone}. Remove the entries."


def test_the_counter_ignores_strings_and_comments() -> None:
    source = (
        "# conn.execute('x')\n"
        "text = \"conn.execute('SELECT 1')\"\n"
        "conn.execute('SELECT 1')\n"
        "cur.executemany('INSERT', rows)\n"
        "conn.executescript('')\n"
        "execute('not a method call')\n"
    )
    assert sql_calls(source) == 3


def test_the_db_package_is_not_counted() -> None:
    assert not any(rel.startswith("db/") for rel in counts())
    assert not any(rel.startswith("db/") for rel in BASELINE)
