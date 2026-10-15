#!/usr/bin/env bash
# Live smoke run in one command: bring up a real stack (production profile, real model, no sim
# overlay), run `python -m live.smoke` against it, tear the stack down. Spends real money, so it
# refuses without CRIBA_REAL_SPEND_CAP_USD (a positive number of USD).
#
#   CRIBA_REAL_SPEND_CAP_USD=3 src/scripts/live_smoke.sh [live.smoke options]
#
# Environment (all optional but the cap):
#   CRIBA_REAL_SPEND_CAP_USD   hard cap in USD; required
#   CRIBA_REAL_SPEND_LEDGER    JSON file carrying the spend across runs (default /tmp/criba-real-spend.json)
#   CRIBA_SMOKE_PROJECT        Compose project name (default criba-smoke); never use a running stack's
#   CRIBA_SMOKE_PORT           web port on the host (default 18160)
#   CRIBA_SMOKE_OUT            where the report goes (default reports/live-smoke.md)
#
# The OpenRouter key must already be in secrets/openrouter_api_key.txt (gitignored; copy it, never
# print it). The other secrets are generated at random when missing. Everything after the script
# name is passed to live.smoke (for example `--flows qualified,stop` or `--note "..."`).
set -euo pipefail

cd "$(dirname "$0")/../.."

: "${CRIBA_REAL_SPEND_CAP_USD:?set CRIBA_REAL_SPEND_CAP_USD to a positive number of USD (a real model is called)}"
project="${CRIBA_SMOKE_PROJECT:-criba-smoke}"
port="${CRIBA_SMOKE_PORT:-18160}"
out="${CRIBA_SMOKE_OUT:-reports/live-smoke.md}"
export CRIBA_REAL_SPEND_LEDGER="${CRIBA_REAL_SPEND_LEDGER:-/tmp/criba-real-spend.json}"

if [ ! -s secrets/openrouter_api_key.txt ] || grep -q 'replace-me' secrets/openrouter_api_key.txt; then
  echo "live_smoke: put the real OpenRouter key in secrets/openrouter_api_key.txt first" >&2
  exit 2
fi
for name in hermes_api_server_key criba_tools_secret criba_booking_secret criba_seed_password; do
  if [ ! -s "secrets/$name.txt" ]; then
    openssl rand -hex 24 > "secrets/$name.txt"
    chmod 644 "secrets/$name.txt"
  fi
done

# A host token must never reach the stack (Compose lists every variable explicitly; this is belt and braces).
unset TELEGRAM_BOT_TOKEN

teardown() {
  docker compose -p "$project" down -v >/dev/null 2>&1 || true
}
trap teardown EXIT

CRIBA_WEB_PORT="$port" docker compose -p "$project" up -d --build --wait

status=0
PYTHONPATH=src/tests CRIBA_E2E_SEED_PASSWORD_FILE=secrets/criba_seed_password.txt \
  uv run --project src/backend python -m live.smoke \
  --base-url "http://localhost:$port" --out "$out" "$@" || status=$?
exit "$status"
