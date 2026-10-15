#!/usr/bin/env bash
# Quality checks from CONSTRAINTS.md. CONSTRAINTS.md is canonical; this script mirrors it.
#
#   src/scripts/check.sh fast [file...]  edit loop: ruff/eslint on the files (all if none), token check on the
#                                        src/web/src files (the changed ones if none), floor guard
#   src/scripts/check.sh task            fast + tsc, the tests related to the changed files (pytest paths,
#                                        `vitest related`) with coverage, changed-line coverage, mypy
#                                        ratchet, gitleaks; under 90 s
#   src/scripts/check.sh full            fast + tsc, the FULL test suites, changed-line coverage, coverage
#                                        and mypy ratchets, gitleaks, Semgrep, osv-scanner, contract gate,
#                                        generated web types, web build (CI)
#   src/scripts/check.sh hook            Claude Code PostToolUse hook: fast on the edited file
#
# Every check runs in the dev container (docker-compose.yml, profile `dev`), never on the host:
# on the host this script re-runs itself there. Checks in WARN_ONLY report but do not fail
# until WARN_UNTIL (see "Enforcement" in CONSTRAINTS.md).
#
# Test selection (task): the changed files are `git diff --name-only <merge base>` plus untracked
# files; src/scripts/select_tests.py maps them to pytest paths and vitest files (rules in its
# docstring, tests in src/backend/tests/scripts/). The project-wide coverage ratchets need the
# whole suites, so they run only in full.
# CHECK_BASE=<ref> compares against that ref instead of main (default main), to measure a lane's
# diff on a branch that is itself ahead of main.
# CRIBA_VITEST_MAX_WORKERS=<n> sets Vitest's worker cap (default min(4, nproc/2), at least 1). Vitest's
# own default is one worker per core, which on a loaded host starves the jsdom suites into hook and
# findBy timeouts; the same tree passed with 4.
set -euo pipefail

WARN_UNTIL=2026-10-23
WARN_ONLY=" coverage mypy semgrep osv "

root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
mode="${1:-}"
base_ref="${CHECK_BASE:-main}"

if [[ "${CRIBA_DEV_CONTAINER:-}" != 1 ]]; then
  # Compose does not forward the host environment, so pass the one variable explicitly.
  env_args=()
  [[ -z "${CHECK_BASE:-}" ]] || env_args=(-e "CHECK_BASE=$CHECK_BASE")
  [[ -z "${CRIBA_VITEST_MAX_WORKERS:-}" ]] || env_args+=(-e "CRIBA_VITEST_MAX_WORKERS=$CRIBA_VITEST_MAX_WORKERS")
  # Compose pins the project name, so every checkout's one-off container would mount the same
  # node_modules volume and concurrent `npm ci` runs would collide. In a linked worktree (git dir
  # differs from the common dir) give the checkout its own volume; the main checkout keeps the
  # default. Compose reads this variable from the environment (docker-compose.yml, volumes).
  if [[ -z "${CRIBA_NODE_MODULES_VOLUME:-}" ]]; then
    git_dir="$(cd "$(git rev-parse --git-dir)" && pwd -P)"
    common_dir="$(cd "$(git rev-parse --git-common-dir)" && pwd -P)"
    if [[ "$git_dir" != "$common_dir" ]]; then
      slug="$(basename "$root" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9_\n-' '-')"
      export CRIBA_NODE_MODULES_VOLUME="criba_nm-${slug}"
    fi
  fi
  # Compose pins the project name, so `ps` lists every dev container of the stack: the long-lived
  # one and the one-off `docker compose run` containers other worktrees' builds start. Inspect each
  # separately and exec only into a long-lived container (oneoff False; a one-off dies with its
  # command) whose /workspace is this checkout. A container that vanishes between `ps` and
  # `inspect` prints nothing and so does not match.
  running=""
  while IFS= read -r cid; do
    [[ -n "$cid" ]] || continue
    info="$(docker inspect "$cid" --format '{{index .Config.Labels "com.docker.compose.oneoff"}}|{{range .Mounts}}{{if eq .Destination "/workspace"}}{{.Source}}{{end}}{{end}}' 2>/dev/null || true)"
    if [[ "$info" == "False|$root" ]]; then running="$cid"; break; fi
  done < <(docker compose --profile dev ps --status running -q dev 2>/dev/null || true)
  if [[ "$mode" == hook ]]; then
    if [[ -z "$running" ]]; then
      echo "check.sh: dev container not running, fast checks skipped (docker compose --profile dev up -d dev)" >&2
      exit 0
    fi
    exec docker exec -i -e CRIBA_HOST_ROOT="$root" "$running" src/scripts/check.sh hook
  fi
  # Under host load, git in the container has intermittently failed to open loose objects written
  # seconds before (a fetch, a commit in a worktree): "unable to open loose object <sha>: Permission
  # denied". Packed objects have not failed. So pack the loose objects written since the last such
  # pack (the stamp), in the object store the container reads (from a worktree, `--git-path` and
  # `git repack` resolve to the common dir). `git count-objects` cannot be the trigger: repack
  # never packs unreachable loose objects, so its count stays high until gc prunes them. A plain
  # `git repack` only adds a pack: without -a/-d it rewrites and deletes nothing, so it is safe
  # while other sessions write to the repo. A failure is reported and the checks run anyway.
  # Best effort under set -e: no fan-out directories (a fully packed clone), a directory pruned
  # mid-scan or a failed git call counts as nothing fresh, never as a reason to stop.
  objects="$(git rev-parse --git-path objects 2>/dev/null || true)"
  stamp="$(git rev-parse --git-common-dir 2>/dev/null || true)/criba-pack-stamp"
  newer=()
  [[ ! -f "$stamp" ]] || newer=(-newer "$stamp")
  fresh=0
  if [[ -n "$objects" && -d "$objects" ]]; then
    fresh="$({ find "$objects" -mindepth 2 -maxdepth 2 -path "$objects/[0-9a-f][0-9a-f]/*" -type f "${newer[@]}" 2>/dev/null || true; } | wc -l | tr -d ' ')"
  fi
  if (( fresh > 0 )); then
    echo "check.sh: packing $fresh loose objects written since the last pack (git repack -q)" >&2
    TIMEFORMAT="check.sh: git repack took %Rs"
    touch "$stamp.$$" 2>/dev/null || true
    if time git repack -q >&2; then
      mv -f "$stamp.$$" "$stamp" 2>/dev/null || true
    else
      rm -f "$stamp.$$"
      echo "check.sh: git repack failed; the checks run anyway" >&2
    fi
  fi
  if [[ -n "$running" ]]; then
    exec docker exec -i ${env_args[@]+"${env_args[@]}"} "$running" src/scripts/check.sh "$@"
  fi
  exec docker compose --profile dev run --rm -T ${env_args[@]+"${env_args[@]}"} dev src/scripts/check.sh "$@"
fi

reports=.coverage-reports
mkdir -p "$reports"
failed=()
warned=()

# gate NAME CMD...: run a check; failure blocks unless NAME is warn-only and WARN_UNTIL is ahead.
gate() {
  local name="$1"; shift
  echo "==> $name"
  if "$@"; then return 0; fi
  if [[ "$WARN_ONLY" == *" $name "* && "$(date -u +%F)" < "$WARN_UNTIL" ]]; then
    warned+=("$name"); echo "    (warn only until $WARN_UNTIL)"
  else
    failed+=("$name")
  fi
}

# measured NAME: the recorded number in a "Measured, not yet enforced" row of CONSTRAINTS.md
# (`| Backend line coverage | ≥ 96.3% | ... |`; the ≥/≤ also tells the floor guard the direction).
measured() {
  python3 - "$1" <<'PY'
import re, sys
for line in open("CONSTRAINTS.md", encoding="utf-8"):
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    if len(cells) > 1 and cells[0] == sys.argv[1]:
        m = re.search(r"\d+(?:\.\d+)?", cells[1])
        print(m.group(0) if m else "")
        break
PY
}

# ratchet NAME VALUE min|max: VALUE must not fall below (min) or rise above (max) the recorded one.
ratchet() {
  local name="$1" value="$2" dir="$3" recorded
  recorded="$(measured "$name")"
  [[ -n "$recorded" ]] || { echo "    no recorded value for '$name' in CONSTRAINTS.md"; return 1; }
  python3 - "$name" "$value" "$recorded" "$dir" <<'PY'
import sys
name, value, recorded, direction = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
tolerance = 0.5 if direction == "min" else 0  # ratchet tolerance: 0.5 points of coverage, none on counts
ok = value >= recorded - tolerance if direction == "min" else value <= recorded
print(f"    {name}: {value:g} (recorded {recorded:g}, must not {'fall' if direction == 'min' else 'grow'})")
sys.exit(0 if ok else 1)
PY
}

line_rate() {
  python3 -c 'import sys, xml.etree.ElementTree as E; print(round(float(E.parse(sys.argv[1]).getroot().get("line-rate")) * 100, 1))' "$1"
}

backend() { uv run --quiet --locked --project src/backend "$@"; }

# node_modules lives in a container volume; install it when package-lock.json changes.
web_deps() {
  local want have=""
  want="$(sha256sum src/web/package-lock.json | cut -d' ' -f1)"
  [[ -f src/web/node_modules/.criba-lock-sha256 ]] && have="$(cat src/web/node_modules/.criba-lock-sha256)"
  [[ "$want" == "$have" ]] && return 0
  echo "==> npm ci (package-lock.json changed)"
  npm --prefix src/web ci --no-audit --no-fund --loglevel=error
  echo "$want" > src/web/node_modules/.criba-lock-sha256
}
merge_base() { git merge-base "$base_ref" HEAD; }

# ruff scope: `ruff check` and `ruff format --check` both cover all Python under src/. With files, the
# format check gets the ones under the format paths. The settings live in src/backend/pyproject.toml;
# src/ruff.toml extends it for src/hermes, src/tests and src/scripts.
ruff_format_paths=(src/backend src/tests src/scripts src/hermes)
ruff_all() { backend ruff check src && backend ruff format --check "${ruff_format_paths[@]}"; }
ruff_files() {
  local f p fmt=()
  backend ruff check "$@" || return 1
  for f in "$@"; do
    for p in "${ruff_format_paths[@]}"; do
      if [[ "$f" == "$p"/* ]]; then fmt+=("$f"); break; fi
    done
  done
  (( ${#fmt[@]} == 0 )) || backend ruff format --check "${fmt[@]}"
}
eslint_files() { (cd src/web && npx --no-install eslint "$@"); }

# web_tokens [file...]: the token check (src/scripts/check_web_tokens.sh). Its self-test runs first, then the
# rules on the src/web/src files among the arguments, or among the changed files when there are no arguments,
# so task and hook get it too. It never scans the whole tree.
web_tokens() {
  src/scripts/check_web_tokens.sh --self-test || return 1
  local files=() f
  if (( $# )); then
    for f in "$@"; do
      if [[ "$f" == src/web/src/* ]]; then files+=("$f"); fi
    done
  else
    while IFS= read -r -d '' f; do
      if [[ "$f" == src/web/src/* ]]; then files+=("$f"); fi
    done < <(changed_files)
  fi
  (( ${#files[@]} == 0 )) || src/scripts/check_web_tokens.sh "${files[@]}"
}

# web_tokens_all: the token check over the whole of src/web/src (tracked, or untracked and not ignored), for
# full. One call gets every file, so the script's own exit status comes back (xargs would turn 1 into 123).
web_tokens_all() {
  src/scripts/check_web_tokens.sh --self-test || return 1
  local files=()
  mapfile -d '' files < <(git ls-files -co --exclude-standard -z -- src/web/src)
  (( ${#files[@]} )) || { echo "web_tokens_all: no files under src/web/src" >&2; return 1; }
  src/scripts/check_web_tokens.sh "${files[@]}"
}

fast() {
  local py=() web=() f
  for f in "$@"; do
    case "$f" in
      src/*.py) py+=("$f") ;;
      src/web/*.ts|src/web/*.tsx|src/web/*.js|src/web/*.mjs) web+=("${f#src/web/}") ;;
    esac
  done
  local only=()
  if (( $# == 0 )); then
    gate ruff ruff_all
    gate eslint eslint_files .
  else
    (( ${#py[@]} )) && gate ruff ruff_files "${py[@]}"
    (( ${#web[@]} )) && gate eslint eslint_files "${web[@]}"
    for f in "$@"; do only+=(--only "$f"); done
  fi
  gate tokens web_tokens "$@"
  # With files, report only their findings: one exception elsewhere on the branch must not
  # block every edit. task and full always run the guard over the whole diff.
  gate floor node src/scripts/floor_guard.mjs --base "$base_ref" "${only[@]}"
}

web_xml=src/web/coverage/cobertura-coverage.xml

# changed_files: tracked changes since the merge base (staged or not, deletions included) plus
# untracked files, NUL-separated, like the floor guard's view of the diff.
changed_files() {
  git diff --name-only -z "$(merge_base)" --
  git ls-files -z --others --exclude-standard
}

# Selection for the tests step, filled in by select_tests.
backend_mode=skip web_mode=skip
backend_targets=() web_files=()

# select_tests SCOPE: full -> both whole suites; related -> what the changed files map to.
# Prints which selection was made and why.
select_tests() {
  local key value listing
  backend_mode=full web_mode=full backend_targets=() web_files=()
  if [[ "$1" == full ]]; then
    echo "    backend: full (check.sh full)"
    echo "    web: full (check.sh full)"
    return 0
  fi
  # A git failure must not read as "nothing changed" and skip every test.
  listing="$(mktemp)"
  changed_files > "$listing" || { echo "    cannot list changed files (git failed)"; rm -f "$listing"; return 1; }
  while IFS=$'\t' read -r key value; do
    case "$key" in
      backend.mode) backend_mode="$value" ;;
      backend.target) backend_targets+=("$value") ;;
      web.mode) web_mode="$value" ;;
      web.file) web_files+=("$value") ;;
      backend.reason) echo "    backend: $value" ;;
      web.reason) echo "    web: $value" ;;
    esac
  done < <(python3 src/scripts/select_tests.py --root . < "$listing")
  rm -f "$listing"
}

pytest_cov() {
  backend pytest -q ${backend_targets[@]+"${backend_targets[@]}"} --cov --cov-config=src/backend/pyproject.toml \
    --cov-report="xml:$reports/backend.xml" --cov-report=term:skip-covered
}
# vitest_workers: the --maxWorkers value, CRIBA_VITEST_MAX_WORKERS or min(4, nproc/2) and at least 1.
vitest_workers() {
  local n="${CRIBA_VITEST_MAX_WORKERS:-}" half
  if [[ ! "$n" =~ ^[1-9][0-9]*$ ]]; then
    half=$(( $(nproc) / 2 ))
    n=$(( half < 4 ? half : 4 ))
    (( n >= 1 )) || n=1
  fi
  echo "$n"
}
vitest_cov() {
  local workers
  workers="$(vitest_workers)"
  echo "    vitest --maxWorkers=$workers"
  if [[ "$web_mode" == full ]]; then
    npm --prefix src/web test -- --coverage --maxWorkers="$workers"
  else
    # Not `npm --prefix src/web exec`: that runs in the current directory and misses vite.config.ts.
    (cd src/web && npx --no-install vitest related --run --coverage --passWithNoTests --maxWorkers="$workers" "${web_files[@]}")
  fi
}

# tests_step SCOPE: select, report the selection, run each side that was not skipped. Reports from
# an earlier run are removed first, so changed_coverage sees only what this run produced.
tests_step() {
  echo "==> test selection"
  rm -f "$reports/backend.xml" "$web_xml"
  if ! select_tests "$1"; then
    failed+=(test-selection)
    return 0
  fi
  [[ "$backend_mode" == skip ]] || gate tests-backend pytest_cov
  [[ "$web_mode" == skip ]] || gate tests-web vitest_cov
}

# changed_coverage: diff-cover over the reports this run produced; a skipped side has none.
changed_coverage() {
  local xml=()
  [[ ! -f "$reports/backend.xml" ]] || xml+=("$reports/backend.xml")
  [[ ! -f "$web_xml" ]] || xml+=("$web_xml")
  if (( ${#xml[@]} == 0 )); then
    echo "    skipped: no tests ran, so there is no coverage report"
    return 0
  fi
  backend diff-cover "${xml[@]}" --compare-branch="$(merge_base)" --fail-under=80 --quiet
}
coverage_ratchets() {
  ratchet "Backend line coverage" "$(line_rate "$reports/backend.xml")" min &&
    ratchet "Web line coverage" "$(line_rate "$web_xml")" min
}
mypy_ratchet() {
  local out count
  out="$(cd src/backend && uv run --quiet mypy 2>&1 || true)"
  count="$(sed -nE 's/^Found ([0-9]+) errors?.*/\1/p' <<<"$out")"
  count="${count:-0}"
  (( count == 0 )) || tail -n 12 <<<"$out" | sed 's/^/    /'
  ratchet "Backend mypy errors" "$count" max
}
# loose_retry CMD...: run CMD, and once more if its stderr shows git could not open a loose object
# (see the host block above; gitleaks logs that and can still exit 0 on a partial scan). Only that
# error text triggers the retry, and the second run's status stands.
loose_retry() {
  local log status=0
  log="$(mktemp)"
  "$@" 2>"$log" || status=$?
  cat "$log" >&2
  if grep -Eq 'unable to open loose object [0-9a-f]+: Permission denied' "$log"; then
    echo "    git could not open a loose object, retrying once: $*"
    status=0
    "$@" || status=$?
  fi
  rm -f "$log"
  return "$status"
}
secrets() {
  loose_retry gitleaks git --redact --no-banner --log-level warn . &&
    loose_retry gitleaks git --pre-commit --redact --no-banner --log-level warn .
}

# checks SCOPE: task passes related; full passes full, because only whole suites give the
# project-wide numbers the coverage ratchets compare.
checks() {
  local scope="$1"
  fast
  gate tsc bash -c 'cd src/web && npx --no-install tsc -b'
  tests_step "$scope"
  gate coverage changed_coverage
  [[ "$scope" == related ]] || gate coverage coverage_ratchets
  gate mypy mypy_ratchet
  gate secrets secrets
}
task() { checks related; }

semgrep_scan() {
  semgrep scan --config p/default --metrics=off --quiet --error --severity ERROR \
    --exclude-rule python.lang.compatibility.python37.python37-compatibility-importlib2 \
    --baseline-commit "$(merge_base)" src
}
osv_scan() { osv-scanner scan source -L src/backend/uv.lock -L src/web/package-lock.json; }

# web_types: the checked-in generated types (src/web/src/api/schema.d.ts, chat-schema.d.ts) equal what
# openapi-typescript makes from the contracts. It generates into $reports/web-types and compares with
# `git diff --no-index --exit-code`, so a drifted tree is reported and left as it is. chat-schema.d.ts
# carries one hand-added header line; it is put on the generated file before the comparison.
web_types() {
  local gen=(npx --yes openapi-typescript@7.13.0) out="$reports/web-types"
  mkdir -p "$out"
  "${gen[@]}" contracts/api.openapi.yaml -o "$out/schema.d.ts" || return 1
  "${gen[@]}" contracts/webchat.openapi.yaml -o "$out/chat-schema.gen.d.ts" || return 1
  {
    echo "/* Generated: npx openapi-typescript contracts/webchat.openapi.yaml -o src/web/src/api/chat-schema.d.ts. Do not edit by hand. */"
    cat "$out/chat-schema.gen.d.ts"
  } >"$out/chat-schema.d.ts"
  git diff --no-index --exit-code -- src/web/src/api/schema.d.ts "$out/schema.d.ts" &&
    git diff --no-index --exit-code -- src/web/src/api/chat-schema.d.ts "$out/chat-schema.d.ts"
}

full() {
  checks full
  gate semgrep semgrep_scan
  gate osv osv_scan
  gate contracts src/scripts/check_contracts.sh
  gate web-types web_types
  gate personas backend python src/scripts/export_personas.py --check
  gate web-tokens-all web_tokens_all
  gate web-build npm --prefix src/web run build
}

hook() {
  local input file
  input="$(cat)"
  file="$(python3 -c 'import json, sys; print(json.loads(sys.stdin.read() or "{}").get("tool_input", {}).get("file_path", ""))' <<<"$input")"
  file="${file#"${CRIBA_HOST_ROOT:-$root}/"}"
  case "$file" in
    ""|/*|.claude/worktrees/*) exit 0 ;;  # outside this checkout, or a lane worktree's own tree
  esac
  [[ -f "$file" ]] || exit 0
  web_deps >&2
  fast "$file" >&2
  if (( ${#failed[@]} )); then
    echo "check.sh: blocked by ${failed[*]} (see CONSTRAINTS.md); fix the code, do not weaken the check" >&2
    exit 2
  fi
  exit 0
}

[[ "$mode" == hook ]] || web_deps

case "$mode" in
  fast) shift; fast "$@" ;;
  task) task ;;
  full) full ;;
  hook) hook ;;
  *) echo "usage: src/scripts/check.sh fast [file...] | task | full | hook" >&2; exit 2 ;;
esac

(( ${#warned[@]} )) && echo "warned (not blocking until $WARN_UNTIL): ${warned[*]}"
if (( ${#failed[@]} )); then
  echo "FAILED: ${failed[*]}"
  exit 1
fi
echo "check.sh $mode: passed"
