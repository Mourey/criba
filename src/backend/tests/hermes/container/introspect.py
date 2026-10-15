"""Runs inside the Hermes container, under the image's Python, with HERMES_HOME set to a profile.

Prints one JSON object describing what that profile's agent would see on the API server:
the resolved toolsets and tool schemas, plus the settings the Criba tests assert on. It makes
no model call. Fed to the interpreter on stdin by tests/hermes/conftest.py.
"""

import json
import os

from hermes_cli.config import load_config
from hermes_cli.plugins import discover_plugins

discover_plugins()

import model_tools  # noqa: E402
from hermes_cli.tools_config import _get_platform_tools  # noqa: E402

config = load_config()
toolsets = sorted(_get_platform_tools(config, "api_server"))
definitions = model_tools.get_tool_definitions(
    enabled_toolsets=toolsets,
    disabled_toolsets=(config.get("agent") or {}).get("disabled_toolsets"),
    quiet_mode=True,
)

print(
    json.dumps(
        {
            "hermes_home": os.environ.get("HERMES_HOME"),
            "toolsets": toolsets,
            "tools": [
                {
                    "name": d["function"]["name"],
                    "toolset": model_tools.get_toolset_for_tool(d["function"]["name"]),
                }
                for d in definitions
            ],
            "definitions": definitions,
            "config": {
                key: config.get(key)
                for key in (
                    "model",
                    "memory",
                    "plugins",
                    "platform_toolsets",
                    "agent",
                    "tools",
                    "auxiliary",
                    "terminal",
                    "platforms",
                    "gateway",
                )
            },
        }
    )
)
