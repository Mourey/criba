"""Run the stub model: `python -m sim.stub_llm [--script PATH] [--host H] [--port P]`.

Serves an OpenAI-compatible API at http://<host>:<port>/v1. Defaults: 0.0.0.0:8000 and the
packaged `scripts/screener.yaml`. Environment: STUB_LLM_SCRIPT, STUB_LLM_PORT, STUB_LLM_HOST,
STUB_LLM_TOOLS_JSON, STUB_LLM_RECORD.
"""

from __future__ import annotations

import argparse
import os
import sys

import uvicorn

from sim.stub_llm.app import DEFAULT_SCRIPT, create_app
from sim.stub_llm.script import ScriptError, ToolsContract, load_script


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sim.stub_llm", description=__doc__)
    parser.add_argument("--script", default=os.environ.get("STUB_LLM_SCRIPT", str(DEFAULT_SCRIPT)))
    parser.add_argument("--host", default=os.environ.get("STUB_LLM_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("STUB_LLM_PORT", "8000")))
    parser.add_argument("--check", action="store_true", help="validate the script and exit")
    args = parser.parse_args(argv)
    contract = ToolsContract.discover()
    try:
        script = load_script(args.script, contract)
    except (ScriptError, OSError) as exc:
        print(f"stub_llm: {exc}", file=sys.stderr)
        return 2
    if args.check:
        checked = "against contracts/tools.json" if contract else "without tools.json"
        print(f"{script.name}: {len(script.rules)} rules ok ({checked})")
        return 0
    uvicorn.run(create_app(script, contract), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
