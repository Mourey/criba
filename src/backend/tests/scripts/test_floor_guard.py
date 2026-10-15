"""src/scripts/floor_guard.mjs against throwaway git repositories (CONSTRAINTS.md, Floor).

The trigger strings are assembled from parts so this file does not trip the guard itself.
Needs node and git, which the dev container and the CI runners have.
"""

import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[4] / "src" / "scripts" / "floor_guard.mjs"
NOQA = "# " + "noqa"
TASK_MARKER = "TO" + "DO"
SKIP_MARK = "@pytest.mark." + "skip"

CONSTRAINTS = """# Constraints

## Enforced with numbers

| Dimension | Rule | Checked by |
|-----------|------|-----------|
| Coverage | ≥ 80% of changed lines | diff-cover |

## Measured, not yet enforced

| Metric | Today | Direction |
|--------|-------|-----------|
| Backend mypy errors | ≤ 8 | must not grow |

## Exceptions

| ID | Rule | Path | Reason | Owner | Expires |
|----|------|------|--------|-------|---------|
| W1 | ruff E402 | `a.py` | reason | owner | 2027-01-07 |
"""


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "CONSTRAINTS.md").write_text(CONSTRAINTS)
    (tmp_path / "app.py").write_text("x = 1\n")
    (tmp_path / "test_app.py").write_text("def test_x():\n    assert 1 == 1\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base")
    git(tmp_path, "switch", "-q", "-c", "work")
    return tmp_path


def guard(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    assert shutil.which("node"), "node is required (dev container, CI)"
    return subprocess.run(
        ["node", str(GUARD), *args], cwd=repo, capture_output=True, text=True, check=False
    )


def test_clean_diff_passes(repo: Path) -> None:
    (repo / "app.py").write_text("x = 2\n")
    result = guard(repo)
    assert result.returncode == 0, result.stderr
    assert "clean" in result.stdout


def test_new_suppression_in_code_blocks(repo: Path) -> None:
    (repo / "app.py").write_text(f"import os  {NOQA}: F401\n")
    result = guard(repo)
    assert result.returncode == 1
    assert "[silenced-checker] app.py" in result.stderr


def test_suppression_citing_a_listed_exception_passes(repo: Path) -> None:
    (repo / "app.py").write_text(f"import os  {NOQA}: E402 -- exception W1\n")
    assert guard(repo).returncode == 0


def test_suppression_citing_an_unknown_exception_blocks(repo: Path) -> None:
    (repo / "app.py").write_text(f"import os  {NOQA}: E402 -- exception W9\n")
    assert guard(repo).returncode == 1


def test_todo_blocks_in_code_but_not_in_docs(repo: Path) -> None:
    (repo / "notes.md").write_text(f"- [ ] {TASK_MARKER}: write the docs\n")
    assert guard(repo).returncode == 0
    (repo / "app.py").write_text(f"x = 1  # {TASK_MARKER} finish\n")
    result = guard(repo)
    assert result.returncode == 1
    assert "[unfinished-work] app.py" in result.stderr


def test_untracked_files_are_checked(repo: Path) -> None:
    (repo / "new.py").write_text("def f():\n    raise " + "NotImplemented" + "Error\n")
    result = guard(repo)
    assert result.returncode == 1
    assert "[unfinished-work] new.py" in result.stderr


def test_added_skip_blocks(repo: Path) -> None:
    (repo / "test_app.py").write_text(f"{SKIP_MARK}\ndef test_x():\n    assert 1 == 1\n")
    result = guard(repo)
    assert result.returncode == 1
    assert "[test-made-easier]" in result.stderr


def test_deleted_test_file_blocks(repo: Path) -> None:
    git(repo, "rm", "-q", "test_app.py")
    result = guard(repo)
    assert result.returncode == 1
    assert "[test-deleted] test_app.py" in result.stderr


def test_removed_assertion_blocks(repo: Path) -> None:
    (repo / "test_app.py").write_text("def test_x():\n    pass\n")
    result = guard(repo)
    assert result.returncode == 1
    assert "[assertion-removed] test_app.py" in result.stderr


def test_lowered_minimum_blocks_and_raised_minimum_passes(repo: Path) -> None:
    path = repo / "CONSTRAINTS.md"
    path.write_text(CONSTRAINTS.replace("≥ 80%", "≥ 70%"))
    result = guard(repo)
    assert result.returncode == 1
    assert "[threshold-loosened]" in result.stderr
    path.write_text(CONSTRAINTS.replace("≥ 80%", "≥ 85%"))
    assert guard(repo).returncode == 0


def test_raised_maximum_blocks_and_lowered_maximum_passes(repo: Path) -> None:
    path = repo / "CONSTRAINTS.md"
    path.write_text(CONSTRAINTS.replace("≤ 8", "≤ 9"))
    assert guard(repo).returncode == 1
    path.write_text(CONSTRAINTS.replace("≤ 8", "≤ 7"))
    assert guard(repo).returncode == 0


def test_new_exception_row_is_reported(repo: Path) -> None:
    row = "| W2 | x | `b.py` | reason | owner | 2027-01-07 |\n"
    (repo / "CONSTRAINTS.md").write_text(CONSTRAINTS + row)
    result = guard(repo)
    assert result.returncode == 1
    assert "[new-exception]" in result.stderr


def test_constraintsignore_skips_listed_paths(repo: Path) -> None:
    (repo / ".constraintsignore").write_text("# shims\nshims/**\n")
    (repo / "shims").mkdir()
    (repo / "shims" / "a.py").write_text(f"import os  {NOQA}: E402\n")
    assert guard(repo).returncode == 0


def test_only_reports_findings_in_the_named_files(repo: Path) -> None:
    (repo / "app.py").write_text(f"import os  {NOQA}: F401\n")
    (repo / "other.py").write_text("y = 1\n")
    assert guard(repo, "--only", "other.py").returncode == 0
    result = guard(repo, "--only", "other.py", "--only", "app.py")
    assert result.returncode == 1
    assert "[silenced-checker] app.py" in result.stderr


def test_missing_base_exits_2(repo: Path) -> None:
    result = guard(repo, "--base", "no-such-ref")
    assert result.returncode == 2
    assert "no merge base" in result.stderr


# Path-scoped exceptions: a row lifts assertion-removed / test-deleted, nothing else.
E_ROW = (
    "| E1 | Floor guard `assertion-removed` and `test-deleted` | `tests/**`, `sim/*.py` "
    "| reason | owner | {expires} |\n"
)
FUTURE, PAST = "2099-12-31", "2000-01-01"


def with_row(repo: Path, row: str) -> None:
    """Put an exception row into the merge base, so it is not itself a new-exception finding."""
    git(repo, "switch", "-q", "main")
    (repo / "CONSTRAINTS.md").write_text(CONSTRAINTS + row)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_a.py").write_text("def test_a():\n    assert 1 == 1\n")
    (repo / "tests" / "test_b.py").write_text("def test_b():\n    assert 2 == 2\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "row")
    git(repo, "switch", "-q", "work")
    git(repo, "reset", "-q", "--hard", "main")


def test_matching_unexpired_row_lifts_removed_assertion_and_deleted_test(repo: Path) -> None:
    with_row(repo, E_ROW.format(expires=FUTURE))
    (repo / "tests" / "test_a.py").write_text("def test_a():\n    pass\n")
    git(repo, "rm", "-q", "tests/test_b.py")
    result = guard(repo)
    assert result.returncode == 0, result.stderr
    assert "lifted by exception E1 [assertion-removed] tests/test_a.py" in result.stdout
    assert "lifted by exception E1 [test-deleted] tests/test_b.py" in result.stdout
    assert "clean" in result.stdout


def test_path_outside_the_row_globs_still_blocks(repo: Path) -> None:
    with_row(repo, E_ROW.format(expires=FUTURE))
    (repo / "test_app.py").write_text("def test_x():\n    pass\n")
    result = guard(repo)
    assert result.returncode == 1
    assert "[assertion-removed] test_app.py" in result.stderr
    assert "lifted" not in result.stdout


def test_single_star_glob_does_not_cross_directories(repo: Path) -> None:
    with_row(repo, E_ROW.format(expires=FUTURE))
    (repo / "sim" / "deep").mkdir(parents=True)
    (repo / "sim" / "test_ok.py").write_text("def test_ok():\n    assert 1\n")
    (repo / "sim" / "deep" / "test_no.py").write_text("def test_no():\n    assert 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "sim tests")
    git(repo, "switch", "-q", "main")
    git(repo, "merge", "-q", "work")
    git(repo, "switch", "-q", "work")
    git(repo, "rm", "-q", "sim/test_ok.py", "sim/deep/test_no.py")
    result = guard(repo)
    assert result.returncode == 1
    assert "[test-deleted] sim/deep/test_no.py" in result.stderr
    assert "[test-deleted] sim/test_ok.py" not in result.stderr
    assert "lifted by exception E1 [test-deleted] sim/test_ok.py" in result.stdout


def test_row_naming_a_different_rule_does_not_lift(repo: Path) -> None:
    other = "| E1 | ruff E402 | `tests/**` | reason | owner | " + FUTURE + " |\n"
    with_row(repo, other)
    git(repo, "rm", "-q", "tests/test_b.py")
    (repo / "tests" / "test_a.py").write_text("def test_a():\n    pass\n")
    result = guard(repo)
    assert result.returncode == 1
    assert "[test-deleted] tests/test_b.py" in result.stderr
    assert "[assertion-removed] tests/test_a.py" in result.stderr


def test_row_naming_one_finding_does_not_lift_the_other(repo: Path) -> None:
    only_assertions = (
        "| E1 | Floor guard `assertion-removed` | `tests/**` | reason | owner | " + FUTURE + " |\n"
    )
    with_row(repo, only_assertions)
    git(repo, "rm", "-q", "tests/test_b.py")
    (repo / "tests" / "test_a.py").write_text("def test_a():\n    pass\n")
    result = guard(repo)
    assert result.returncode == 1
    assert "[test-deleted] tests/test_b.py" in result.stderr
    assert "[assertion-removed] tests/test_a.py" not in result.stderr


def test_expired_row_lifts_nothing_and_is_reported(repo: Path) -> None:
    with_row(repo, E_ROW.format(expires=PAST))
    git(repo, "rm", "-q", "tests/test_b.py")
    (repo / "tests" / "test_a.py").write_text("def test_a():\n    pass\n")
    result = guard(repo)
    assert result.returncode == 1
    assert "[test-deleted] tests/test_b.py" in result.stderr
    assert "[assertion-removed] tests/test_a.py" in result.stderr
    assert "[exception-expired] CONSTRAINTS.md: E1 expires 2000-01-01" in result.stderr
    assert "lifted" not in result.stdout


def test_expired_row_is_reported_even_without_other_findings(repo: Path) -> None:
    with_row(repo, E_ROW.format(expires=PAST))
    result = guard(repo)
    assert result.returncode == 1
    assert "[exception-expired]" in result.stderr


def test_row_expiring_today_still_lifts(repo: Path) -> None:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    with_row(repo, E_ROW.format(expires=today))
    git(repo, "rm", "-q", "tests/test_b.py")
    result = guard(repo)
    assert result.returncode == 0, result.stderr
    assert "lifted by exception E1 [test-deleted] tests/test_b.py" in result.stdout


def test_suppression_citing_a_path_scoped_exception_still_works(repo: Path) -> None:
    with_row(repo, E_ROW.format(expires=FUTURE))
    (repo / "app.py").write_text(f"import os  {NOQA}: E402 -- exception E1\n")
    assert guard(repo).returncode == 0


def test_path_scoped_row_does_not_lift_other_findings(repo: Path) -> None:
    with_row(repo, E_ROW.format(expires=FUTURE))
    (repo / "tests" / "test_a.py").write_text(f"{SKIP_MARK}\ndef test_a():\n    assert 1 == 1\n")
    result = guard(repo)
    assert result.returncode == 1
    assert "[test-made-easier] tests/test_a.py" in result.stderr


def test_newly_added_path_scoped_row_is_still_reported(repo: Path) -> None:
    (repo / "CONSTRAINTS.md").write_text(CONSTRAINTS + E_ROW.format(expires=FUTURE))
    (repo / "tests").mkdir()
    (repo / "tests" / "test_a.py").write_text("def test_a():\n    pass\n")
    result = guard(repo)
    assert result.returncode == 1
    assert "[new-exception] CONSTRAINTS.md" in result.stderr


# Edited assertions: a removed assertion pairs with an added one about the same subject in the
# same function or it/test block, and a pair is assertion-changed (printed, not failing).
COUNT_PY = "def test_count():\n    registered = [1] * 8\n    assert len(registered) == 8\n"
COUNT_TS = (
    "describe('tools', () => {\n"
    "  it('counts', () => {\n    expect(list.length).toBe(8);\n  });\n"
    "});\n"
)


def with_tests(repo: Path, **files: str) -> None:
    """Commit test files into the merge base and return to `work`; `__` in a name becomes a dot."""
    git(repo, "switch", "-q", "main")
    for name, content in files.items():
        (repo / name.replace("__", ".")).write_text(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "tests")
    git(repo, "switch", "-q", "work")
    git(repo, "reset", "-q", "--hard", "main")


def test_changed_value_on_the_same_subject_is_changed_not_removed(repo: Path) -> None:
    with_tests(repo, test_count__py=COUNT_PY)
    (repo / "test_count.py").write_text(COUNT_PY.replace("== 8", "== 9"))
    result = guard(repo)
    assert result.returncode == 0, result.stderr
    assert "assertion-changed test_count.py [test_count]" in result.stdout
    assert "len(registered) == 8  ->  assert len(registered) == 9" in result.stdout
    assert "clean" in result.stdout


def test_stricter_payload_assertion_is_changed_not_removed(repo: Path) -> None:
    before = 'def test_payload():\n    payload = build()\n    assert payload == {"a": 1}\n'
    after = 'def test_payload():\n    payload = build()\n    assert payload == {"a": 1, "b": 2}\n'
    with_tests(repo, test_payload__py=before)
    (repo / "test_payload.py").write_text(after)
    result = guard(repo)
    assert result.returncode == 0, result.stderr
    assert "assertion-changed test_payload.py [test_payload]" in result.stdout
    assert "assertion-removed" not in result.stderr


def test_removed_assertion_without_an_added_one_in_the_function_still_fails(repo: Path) -> None:
    before = "def test_two():\n    assert a == 1\n    assert b == 2\n"
    with_tests(repo, test_two__py=before)
    (repo / "test_two.py").write_text("def test_two():\n    assert a == 5\n")
    result = guard(repo)
    assert result.returncode == 1
    assert "[assertion-removed] test_two.py: assert b == 2" in result.stderr
    assert "[assertion-removed] test_two.py: assert a == 1" not in result.stderr
    assert "assertion-changed test_two.py [test_two]" in result.stdout


def test_two_removed_on_one_subject_and_one_added_is_a_net_loss(repo: Path) -> None:
    before = "def test_n():\n    assert n == 1\n    assert n == 2\n"
    with_tests(repo, test_n__py=before)
    (repo / "test_n.py").write_text("def test_n():\n    assert n == 3\n")
    result = guard(repo)
    assert result.returncode == 1
    assert result.stderr.count("[assertion-removed] test_n.py") == 1
    assert result.stdout.count("assertion-changed") == 1


def test_assertion_moved_to_a_different_function_still_fails(repo: Path) -> None:
    before = "def test_a():\n    assert x == 1\n\n\ndef test_b():\n    assert y == 1\n"
    after = "def test_a():\n    pass\n\n\ndef test_b():\n    assert y == 1\n    assert x == 1\n"
    with_tests(repo, test_moved__py=before)
    (repo / "test_moved.py").write_text(after)
    result = guard(repo)
    assert result.returncode == 1
    assert "[assertion-removed] test_moved.py: assert x == 1" in result.stderr
    assert "assertion-changed" not in result.stdout


def test_assertion_on_a_different_subject_still_fails(repo: Path) -> None:
    with_tests(repo, test_count__py=COUNT_PY)
    (repo / "test_count.py").write_text(COUNT_PY.replace("len(registered) == 8", "len(other) == 8"))
    result = guard(repo)
    assert result.returncode == 1
    assert "[assertion-removed] test_count.py" in result.stderr
    assert "assertion-changed" not in result.stdout


def test_subject_is_compared_without_whitespace_differences(repo: Path) -> None:
    with_tests(repo, test_count__py=COUNT_PY)
    (repo / "test_count.py").write_text(
        COUNT_PY.replace("len(registered) == 8", "len( registered )  ==  9")
    )
    result = guard(repo)
    assert result.returncode == 0, result.stderr
    assert "assertion-changed" in result.stdout


def test_python_multi_line_signature_and_assert_message(repo: Path) -> None:
    before = (
        "class TestA:\n    def test_x(\n        self,\n        fix,\n    ) -> None:\n"
        "        assert fix.n == 1, 'one'\n"
    )
    with_tests(repo, test_sig__py=before)
    (repo / "test_sig.py").write_text(before.replace("== 1, 'one'", "== 2, 'two'"))
    result = guard(repo)
    assert result.returncode == 0, result.stderr
    assert "[TestA > test_x]" in result.stdout


def test_typescript_changed_value_and_stricter_payload_are_changed(repo: Path) -> None:
    before = COUNT_TS.replace("  });\n});", "    expect(res.body).toEqual({ a: 1 });\n  });\n});")
    after = before.replace("toBe(8)", "toBe(9)").replace("{ a: 1 }", "{ a: 1, b: 2 }")
    with_tests(repo, counts__test__ts=before)
    (repo / "counts.test.ts").write_text(after)
    result = guard(repo)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("assertion-changed counts.test.ts [describe tools > it counts]") == 2


def test_typescript_assertion_moved_to_another_it_block_still_fails(repo: Path) -> None:
    before = (
        "describe('t', () => {\n"
        "  it('one', () => {\n    expect(a).toBe(1);\n  });\n"
        "  it('two', () => {\n    expect(b).toBe(1);\n  });\n"
        "});\n"
    )
    after = (
        "describe('t', () => {\n"
        "  it('one', () => {\n  });\n"
        "  it('two', () => {\n    expect(b).toBe(1);\n    expect(a).toBe(1);\n  });\n"
        "});\n"
    )
    with_tests(repo, moved__test__ts=before)
    (repo / "moved.test.ts").write_text(after)
    result = guard(repo)
    assert result.returncode == 1
    assert "[assertion-removed] moved.test.ts: expect(a).toBe(1);" in result.stderr


def test_typescript_different_subject_still_fails(repo: Path) -> None:
    with_tests(repo, counts__test__ts=COUNT_TS)
    (repo / "counts.test.ts").write_text(COUNT_TS.replace("list.length", "items.length"))
    result = guard(repo)
    assert result.returncode == 1
    assert "[assertion-removed] counts.test.ts" in result.stderr


def test_exception_row_still_lifts_unpaired_removal_and_changed_stays_informational(
    repo: Path,
) -> None:
    with_row(repo, E_ROW.format(expires=FUTURE))
    git(repo, "switch", "-q", "main")
    (repo / "tests" / "test_a.py").write_text(
        "def test_a():\n    assert x == 1\n    assert y == 2\n"
    )
    git(repo, "commit", "-q", "-am", "two assertions")
    git(repo, "switch", "-q", "work")
    git(repo, "reset", "-q", "--hard", "main")
    (repo / "tests" / "test_a.py").write_text("def test_a():\n    assert x == 5\n")
    result = guard(repo)
    assert result.returncode == 0, result.stderr
    assert "assertion-changed tests/test_a.py [test_a]" in result.stdout
    assert "lifted by exception E1 [assertion-removed] tests/test_a.py" in result.stdout
    assert "(x" not in result.stdout


def test_only_filters_assertion_changed_lines(repo: Path) -> None:
    with_tests(repo, test_count__py=COUNT_PY)
    (repo / "test_count.py").write_text(COUNT_PY.replace("== 8", "== 9"))
    assert "assertion-changed" not in guard(repo, "--only", "app.py").stdout
    assert "assertion-changed" in guard(repo, "--only", "test_count.py").stdout


# Whole statements: a pure re-wrap or reformat of an assertion (ruff format, prettier) is no
# finding at all, and the pairing of edited assertions still works on multi-line statements.
WRAPPED_PY = (
    "def test_wrapped():\n"
    "    assert (\n"
    "        some_call(x)\n"
    "        == expected\n"
    "    )\n"
    "    assert other(\n"
    "        1,\n"
    "        2,\n"
    "    ) == [3]  # why\n"
)
FLAT_PY = (
    "def test_wrapped():\n    assert some_call(x) == expected\n    assert other(1, 2) == [3]\n"
)
WRAPPED_TS = (
    "describe('t', () => {\n"
    "  it('wraps', () => {\n"
    "    expect(\n      res.body,\n    ).toEqual({\n      a: 1,\n    });\n"
    "    expect(screen.getByRole('button'))\n      .not\n      .toBeDisabled();\n"
    "  });\n"
    "});\n"
)
FLAT_TS = (
    "describe('t', () => {\n"
    "  it('wraps', () => {\n"
    "    expect(res.body).toEqual({ a: 1 });\n"
    '    expect(screen.getByRole("button")).not.toBeDisabled();\n'
    "  });\n"
    "});\n"
)


def clean_after(repo: Path, name: str, before: str, after: str) -> subprocess.CompletedProcess[str]:
    with_tests(repo, **{name: before})
    (repo / name.replace("__", ".")).write_text(after)
    return guard(repo)


def test_a_multi_line_assert_reflowed_to_one_line_is_clean(repo: Path) -> None:
    result = clean_after(repo, "test_wrapped__py", WRAPPED_PY, FLAT_PY)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "floor-guard: clean"


def test_a_one_line_assert_wrapped_by_the_formatter_is_clean(repo: Path) -> None:
    result = clean_after(repo, "test_wrapped__py", FLAT_PY, WRAPPED_PY)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "floor-guard: clean"


def test_a_different_wrapping_of_a_multi_line_assert_is_clean(repo: Path) -> None:
    other = WRAPPED_PY.replace(
        "        some_call(x)\n        == expected\n",
        "        some_call(\n            x\n        ) == expected,\n",
    ).replace("expected,\n", "expected\n")
    result = clean_after(repo, "test_wrapped__py", WRAPPED_PY, other)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "floor-guard: clean"


def test_message_parentheses_quotes_and_trailing_commas_do_not_matter(repo: Path) -> None:
    before = (
        "def test_m():\n    assert x == 'a', (\n        'why'\n    )\n"
        "    assert f(a, b,) == {'k': [1, 2,],}\n"
    )
    after = (
        'def test_m():\n    assert (\n        x == "a"\n    ), "why"\n'
        '    assert f(a, b) == {"k": [1, 2]}\n'
    )
    result = clean_after(repo, "test_m__py", before, after)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "floor-guard: clean"


def test_swapping_the_sides_of_an_equality_is_clean(repo: Path) -> None:
    result = clean_after(
        repo,
        "test_y__py",
        "def test_y():\n    assert (1, [2]) == got()\n",
        "def test_y():\n    assert got() == (1, [2])\n",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "floor-guard: clean"


def test_a_multi_line_expect_chain_reflowed_is_clean(repo: Path) -> None:
    result = clean_after(repo, "wrapped__test__ts", WRAPPED_TS, FLAT_TS)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "floor-guard: clean"
    result = clean_after(repo, "wrapped__test__ts", FLAT_TS, WRAPPED_TS)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "floor-guard: clean"


def test_a_rewrap_with_a_real_change_is_changed_not_removed(repo: Path) -> None:
    result = clean_after(repo, "test_wrapped__py", WRAPPED_PY, FLAT_PY.replace("== [3]", "== [4]"))
    assert result.returncode == 0, result.stderr
    assert "assertion-changed test_wrapped.py [test_wrapped]" in result.stdout
    assert "[3]" in result.stdout
    assert "assertion-removed" not in result.stderr


def test_a_changed_value_inside_a_multi_line_assert_is_reported_as_changed(repo: Path) -> None:
    result = clean_after(
        repo,
        "test_wrapped__py",
        WRAPPED_PY,
        WRAPPED_PY.replace("== expected\n", "== expected_now\n"),
    )
    assert result.returncode == 0, result.stderr
    assert "assertion-changed test_wrapped.py [test_wrapped]" in result.stdout


def test_a_rewrap_with_a_real_change_in_a_chain_is_changed(repo: Path) -> None:
    result = clean_after(repo, "wrapped__test__ts", WRAPPED_TS, FLAT_TS.replace("a: 1", "a: 2"))
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("assertion-changed wrapped.test.ts") == 1


def test_a_one_element_tuple_losing_its_comma_is_a_change(repo: Path) -> None:
    result = clean_after(
        repo,
        "test_t__py",
        "def test_t():\n    assert got() == (1,)\n",
        "def test_t():\n    assert got() == (1)\n",
    )
    assert result.returncode == 0, result.stderr
    assert "assertion-changed test_t.py [test_t]" in result.stdout


def test_deleting_an_assertion_from_a_rewrapped_block_is_still_removed(repo: Path) -> None:
    after = "def test_wrapped():\n    assert some_call(x) == expected\n"
    result = clean_after(repo, "test_wrapped__py", WRAPPED_PY, after)
    assert result.returncode == 1
    assert "[assertion-removed] test_wrapped.py: assert other(" in result.stderr
    assert "some_call" not in result.stderr


def test_deleting_the_wrapped_assertion_keeps_the_flat_one_clean_of_blame(repo: Path) -> None:
    after = "def test_wrapped():\n    assert other(1, 2) == [3]\n"
    result = clean_after(repo, "test_wrapped__py", WRAPPED_PY, after)
    assert result.returncode == 1
    assert result.stderr.count("[assertion-removed]") == 1
    assert "[assertion-removed] test_wrapped.py: assert some_call(x) == expected" in result.stderr


def test_deleting_a_whole_multi_line_assertion_is_removed(repo: Path) -> None:
    result = clean_after(repo, "test_wrapped__py", WRAPPED_PY, "def test_wrapped():\n    pass\n")
    assert result.returncode == 1
    assert result.stderr.count("[assertion-removed] test_wrapped.py") == 2


def test_deleting_a_multi_line_expect_chain_is_removed(repo: Path) -> None:
    after = WRAPPED_TS.replace(
        "    expect(\n      res.body,\n    ).toEqual({\n      a: 1,\n    });\n", ""
    )
    result = clean_after(repo, "wrapped__test__ts", WRAPPED_TS, after)
    assert result.returncode == 1
    assert "[assertion-removed] wrapped.test.ts: expect(res.body).toEqual({a: 1});" in result.stderr


def test_two_wrapped_assertions_collapsing_into_one_is_a_net_loss(repo: Path) -> None:
    before = (
        "def test_d():\n    assert (\n        f(x)\n        == 1\n    )\n    assert f(x) == 1\n"
    )
    result = clean_after(repo, "test_d__py", before, "def test_d():\n    assert f(x) == 1\n")
    assert result.returncode == 1
    assert result.stderr.count("[assertion-removed] test_d.py") == 1


def test_a_wrapped_assertion_moved_to_another_function_still_fails(repo: Path) -> None:
    before = (
        "def test_a():\n    assert (\n        x\n        == 1\n    )\n\n\n"
        "def test_b():\n    assert y == 1\n"
    )
    after = "def test_a():\n    pass\n\n\ndef test_b():\n    assert y == 1\n    assert x == 1\n"
    result = clean_after(repo, "test_mv__py", before, after)
    assert result.returncode == 1
    assert "[assertion-removed] test_mv.py: assert x == 1" in result.stderr


def test_a_reformatted_call_that_only_mentions_expect_is_clean(repo: Path) -> None:
    before = (
        "def test_p():\n    got = run(\n        persona.expect,\n        [1],\n    )\n"
        "    assert got\n"
    )
    after = "def test_p():\n    got = run(persona.expect, [1])\n    assert got\n"
    result = clean_after(repo, "test_p__py", before, after)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "floor-guard: clean"


# A git shim on PATH fails `git diff --unified=0 <base> --` the first FAILS times with ERROR,
# counting calls, then runs the real git: the loose-object retry (check.sh's second line of
# defence against Docker file sharing) without Docker.
LOOSE_DENIED = (
    "error: unable to open loose object 0123456789abcdef0123456789abcdef01234567: Permission denied"
)


def guard_with_git_shim(
    repo: Path, tmp_path: Path, error: str, fails: int
) -> tuple[subprocess.CompletedProcess[str], int]:
    real_git = shutil.which("git")
    assert real_git, "git is required"
    shim_dir, calls = tmp_path / "shim", tmp_path / "diff-calls"
    shim_dir.mkdir()
    shim = shim_dir / "git"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == diff && "$2" == --unified=0 && "$4" == -- ]]; then\n'
        f'  echo x >> "{calls}"\n'
        f'  if (( $(wc -l < "{calls}") <= {fails} )); then echo "{error}" >&2; exit 128; fi\n'
        "fi\n"
        f'exec "{real_git}" "$@"\n'
    )
    shim.chmod(0o755)
    env = {**os.environ, "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"}
    result = subprocess.run(
        ["node", str(GUARD)], cwd=repo, capture_output=True, text=True, check=False, env=env
    )
    return result, len(calls.read_text().splitlines()) if calls.exists() else 0


@pytest.fixture
def work(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    (repo / "app.py").write_text("x = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "switch", "-q", "-c", "work")
    (repo / "app.py").write_text("x = 2\n")
    return repo


def test_a_loose_object_denied_once_is_retried(work: Path, tmp_path: Path) -> None:
    result, calls = guard_with_git_shim(work, tmp_path, LOOSE_DENIED, fails=1)
    assert result.returncode == 0, result.stderr
    assert "retrying once" in result.stderr
    assert calls == 2


def test_a_loose_object_denied_twice_still_bails(work: Path, tmp_path: Path) -> None:
    result, calls = guard_with_git_shim(work, tmp_path, LOOSE_DENIED, fails=2)
    assert result.returncode == 2
    assert "could not diff against" in result.stderr
    assert calls == 2


def test_another_git_error_is_not_retried(work: Path, tmp_path: Path) -> None:
    error = "fatal: unable to read 0123456789abcdef0123456789abcdef01234567"
    result, calls = guard_with_git_shim(work, tmp_path, error, fails=1)
    assert result.returncode == 2
    assert "retrying" not in result.stderr
    assert calls == 1
