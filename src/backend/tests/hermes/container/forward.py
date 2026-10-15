"""Runs inside the Hermes container, under the image's Python, with HERMES_HOME set to a profile.

Starts a recording HTTP server on 127.0.0.1 playing the backend, loads the profile's plugins
the way the gateway does, then calls tools through ``model_tools.handle_function_call``, the
same entry the agent loop uses, passing the ``session_id`` a Runs API turn would carry.
Prints the results and every request the server saw as one JSON object. No model call.

Configuration comes from the command line: ``forward.py <scenario-json>``, where the scenario
is ``{"calls": [{"tool", "args", "session_id"}...], "responses": {"<tool>": {"status", "body"}}}``.
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

scenario = json.loads(sys.argv[1])
recorded = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        recorded.append(
            {
                "method": "POST",
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "content_type": self.headers.get("Content-Type"),
                "body": json.loads(raw),
            }
        )
        name = self.path.rsplit("/", 1)[-1]
        planned = scenario.get("responses", {}).get(name) or {
            "status": 200,
            "body": {"ok": True, "value": {"stub": name}},
        }
        payload = json.dumps(planned["body"]).encode()
        self.send_response(planned["status"])
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()

os.environ["CRIBA_BACKEND_URL"] = f"http://127.0.0.1:{server.server_address[1]}"
os.environ["CRIBA_TOOLS_SECRET"] = scenario["secret"]

from hermes_cli.plugins import discover_plugins  # noqa: E402

discover_plugins()

import model_tools  # noqa: E402

results = []
for call in scenario["calls"]:
    raw = model_tools.handle_function_call(
        call["tool"],
        call["args"],
        task_id=call.get("task_id", call.get("session_id")),
        session_id=call.get("session_id"),
    )
    results.append(json.loads(raw) if isinstance(raw, str) else raw)

server.shutdown()
print(json.dumps({"results": results, "requests": recorded}))
