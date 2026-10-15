"""Create, update and check the ES and MX ElevenLabs agents from one config (spec voice-agent
agents-config, AG1-AG7; R18).

    uv run --project src/backend python src/scripts/voice_agents.py \\
        [--check | --apply [--rotate-llm-secret]] [--post-call-webhook-url URL]

The config and the comparison live in ``criba.channel.voice_agents_config``; this script parses
the arguments and maps the outcome to an exit code. The variables are the backend's
(``CRIBA_VOICE_AGENT_ES``, ``CRIBA_VOICE_AGENT_MX``, ``CRIBA_VOICE_PUBLIC_BASE_URL``), and the
ElevenLabs key and the custom-LLM secret are read from the files ``CRIBA_ELEVENLABS_API_KEY_FILE``
and ``CRIBA_VOICE_LLM_SECRET_FILE`` name. No secret is taken from argv or printed.

Modes:

- no flag: print what ``--apply`` would change; write nothing; exit 0.
- ``--check``: read only; exit 0 when both agents match, 1 with one line per drifted field.
- ``--apply``: create the workspace secret ``criba_voice_llm_secret`` if missing, patch only
  the drifted fields of each agent, or create an agent whose id is unset (refused when an
  agent with its name already exists), then read the agents again; exit 0 when they match,
  1 when drift remains (a phone number, a WhatsApp account or a workflow must be removed in
  ElevenLabs by hand).

Exit 2 means it cannot decide or refused: a missing key, an unset or unsafe variable, an HTTP
error, a timeout, an unparseable body, the duplicate-name guard, or no workspace webhook with
the given URL. Drift lines read ``<country> <dotted path>: <live> -> <desired>``; URL- and
secret-valued fields print ``<redacted>``.

Owner steps:

- **Rotating the custom-LLM secret.** GET cannot show a secret's value, so ``--check`` cannot
  see a value changed in the console. After changing ``secrets/criba_voice_llm_secret.txt``,
  run ``--apply --rotate-llm-secret`` to replace the workspace secret's value.
- **The post-call webhook** (decisions 18, 40). Add the webhook in ElevenLabs with its URL
  (HMAC) and copy its signing secret into ``secrets/elevenlabs_webhook_secret.txt``; this
  script never creates one. ``--post-call-webhook-url URL`` (https only) then finds it by URL
  and sets its id on both agents (``platform_settings.workspace_overrides.webhooks.
  post_call_webhook_id``). The workspace-wide setting is never written. Without the argument
  the webhook id is neither compared nor changed.
- **Published or draft.** ElevenLabs versions agents. Probe's PATCH changed the next calls
  (spike 005, Departures), so a PATCH is taken as live; the first owner-run ``--apply`` then
  ``--check`` confirms it on the real account.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

import httpx

from criba.channel.voice_agents_config import AgentCheckError, apply_agents, dry_run, read_drift
from criba.channel.voice_config import VoiceSettings


def parser() -> argparse.ArgumentParser:
    parse = argparse.ArgumentParser(
        prog="voice_agents.py", description="Create, update and check the ElevenLabs agents."
    )
    mode = parse.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="read only; exit 0, 1 or 2")
    mode.add_argument("--apply", action="store_true", help="create or patch the agents")
    parse.add_argument(
        "--rotate-llm-secret",
        action="store_true",
        help="with --apply: replace the workspace secret's value from its file",
    )
    parse.add_argument(
        "--post-call-webhook-url",
        metavar="URL",
        help="https URL of the existing post-call webhook to set on both agents",
    )
    return parse


def main(argv: Sequence[str] | None = None, *, transport: httpx.BaseTransport | None = None) -> int:
    parse = parser()
    args = parse.parse_args(argv)
    if args.rotate_llm_secret and not args.apply:
        parse.error("--rotate-llm-secret needs --apply")
    settings = VoiceSettings.from_env()
    url = args.post_call_webhook_url
    try:
        if args.check:
            drifts = read_drift(settings, transport=transport, webhook_url=url)
            for drift in drifts:
                print(drift.line())
            return 1 if drifts else 0
        if args.apply:
            result = apply_agents(
                settings,
                transport=transport,
                rotate_llm_secret=args.rotate_llm_secret,
                webhook_url=url,
            )
            for country, agent_id in result.created.items():
                print(f"{country} created agent {agent_id}: set CRIBA_VOICE_AGENT_{country} to it")
            for line in result.remaining:
                print(line)
            return 1 if result.remaining else 0
        for line in dry_run(settings, transport=transport, webhook_url=url):
            print(line)
        return 0
    except AgentCheckError as exc:
        print(f"voice_agents: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
