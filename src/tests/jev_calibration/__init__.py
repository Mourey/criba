"""Calibration of the Jev questions and thresholds on labelled messages (ADR 0051).

From the repo root::

    export PYTHONPATH=src/tests CRIBA_REAL_SPEND_CAP_USD=5.00
    export CRIBA_REAL_SPEND_LEDGER=/tmp/criba-real-spend.json
    run="uv run --project src/backend python -m jev_calibration"
    $run collect --out /tmp/jev-cal.json --splits dev
    $run show /tmp/jev-cal.json
    $run report /tmp/jev-cal.json --out /tmp/jev-calibration.md

``collect`` calls the real service through OpenRouter (the key file is
``CRIBA_OPENROUTER_API_KEY_FILE``, default ``secrets/openrouter_api_key.txt``).
"""
