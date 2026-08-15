"""Run a generated scenario against a real Vapi assistant.

Three pieces, because a live voice test needs all three and the harness already owns the data for
each:

1. A **mock tool server**. Vapi executes an assistant's tools by calling a public webhook, so the
   scenario's ``mock_responses`` are served over HTTP rather than in-process. The server also
   records every call it answers, with arguments, which is what the deterministic checkpoints are
   graded against afterwards. The recording is the point: provider evidence can lag or drop, and a
   test that cannot be graded is not a test.

2. An **assistant registry**. Assistants are created once per agent and reused, so the id lives in
   a local file rather than being minted fresh on every run. Creating a new assistant per run would
   litter the account and lose the tool wiring.

3. A **scenario binding**. The mock server serves one scenario at a time; binding swaps which
   ``mock_responses`` are live without touching the assistant.

Nothing here decides pass or fail. Grading stays in ``checks.py``, against the calls this server
recorded plus the transcript the run produced.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Mapping

from .contract import AgentContract

logger = logging.getLogger(__name__)

REGISTRY_PATH = os.environ.get(
    "ALK_VAPI_REGISTRY", "artifacts/scenario-gen/vapi_assistants.json"
)
VAPI_API_BASE = os.environ.get("VAPI_API_BASE_URL", "https://api.vapi.ai").rstrip("/")


# ----------------------------------------------------------------------------------
# The mock tool server
# ----------------------------------------------------------------------------------


@dataclass
class ToolCallLog:
    """Every tool call the assistant made, in order, with the arguments it passed."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, name: str, arguments: Mapping[str, Any]) -> None:
        with self._lock:
            self.calls.append({"name": name, "arguments": dict(arguments)})

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(c) for c in self.calls]

    def reset(self) -> None:
        with self._lock:
            self.calls.clear()


class ScenarioMockServer:
    """Serves one scenario's mock tool responses over HTTP, and records what was called."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.log = ToolCallLog()
        self._scenario: dict[str, Any] = {}
        self._state: dict[str, Any] = {}
        try:
            server = HTTPServer((host, port), _make_handler(self))
        except OSError:
            # A leftover server from an earlier run must not block this one; any free port
            # works because the public URL is discovered after binding.
            logger.warning("port %s busy, binding an ephemeral port instead", port)
            server = HTTPServer((host, 0), _make_handler(self))
        self._server = server
        self.port = server.server_address[1]
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)

    # -- lifecycle ---------------------------------------------------------------
    def start(self) -> "ScenarioMockServer":
        self._thread.start()
        logger.info("mock tool server listening on port %s", self.port)
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    # -- scenario binding --------------------------------------------------------
    def bind(self, record: Mapping[str, Any]) -> None:
        """Make one scenario's mocks live, and clear anything the previous one recorded."""
        environment = record.get("environment") or {}
        self._scenario = dict(record)
        self._state = json.loads(json.dumps(environment.get("seed") or {}))
        self.log.reset()

    @property
    def final_state(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._state))

    # -- the actual mock ---------------------------------------------------------
    def respond(self, name: str, arguments: Mapping[str, Any]) -> Any:
        self.log.record(name, arguments)
        mocks = (self._scenario.get("environment") or {}).get("mock_responses") or {}
        mock = mocks.get(name)
        if not isinstance(mock, Mapping):
            # A tool the scenario did not mock still has to answer, or the assistant stalls
            # mid-call and the conversation dies for a reason unrelated to what is being tested.
            logger.warning(
                "no mock declared for %s; answering with an acknowledgement", name
            )
            return f"{name} completed."
        updates = mock.get("state_updates")
        if isinstance(updates, Mapping):
            _deep_merge(self._state, updates)
        content = mock.get("content", mock.get("result"))
        return content if content is not None else f"{name} completed."


def _deep_merge(target: dict[str, Any], updates: Mapping[str, Any]) -> None:
    for key, value in updates.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = json.loads(json.dumps(value))


def _make_handler(owner: "ScenarioMockServer"):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # silence per-request stderr noise
            return

        def do_POST(self) -> None:  # noqa: N802 - required name
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                payload = {}
            results = [
                {"toolCallId": call_id, "result": owner.respond(name, arguments)}
                for call_id, name, arguments in _tool_calls(payload)
            ]
            body = json.dumps({"results": results}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _tool_calls(payload: Mapping[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """Pull (id, name, arguments) out of a Vapi tool-call webhook body."""
    message = payload.get("message") or payload
    raw = message.get("toolCalls") or message.get("toolCallList") or []
    calls: list[tuple[str, str, dict[str, Any]]] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, Mapping):
            continue
        function = entry.get("function") or {}
        name = str(function.get("name") or entry.get("name") or "")
        arguments = function.get("arguments") or entry.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {"_raw": arguments}
        if name:
            calls.append((str(entry.get("id") or ""), name, dict(arguments)))
    return calls


# ----------------------------------------------------------------------------------
# The assistant: built from the contract, registered once, reused
# ----------------------------------------------------------------------------------


def assistant_payload(
    contract: AgentContract, *, tool_base_url: str, name: str
) -> dict[str, Any]:
    """A Vapi assistant that behaves like the agent the contract describes.

    The system prompt and the tool surface both come from the contract, so the assistant under
    test is the agent under test rather than an approximation of it.
    """
    rules = "\n".join(f"- {rule}" for rule in contract.hard_constraints)
    system = (
        f"{contract.system_prompt_excerpt}\n\n"
        f"Rules you always follow:\n{rules}\n\n"
        "You are speaking to a customer over a voice channel. Keep replies short and natural. "
        "Call the tools you have been given to actually place, change or read the order; never "
        "claim an action you have not performed through a tool."
    )
    tools = []
    for tool in contract.tools:
        properties = {}
        for arg in tool.args:
            allowed = (tool.arg_values or {}).get(arg)
            schema: dict[str, Any] = {"type": "string"}
            if isinstance(allowed, list) and allowed:
                schema["enum"] = [str(v) for v in allowed][:40]
            properties[arg] = schema
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or tool.name,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": list(tool.args),
                    },
                },
                "server": {"url": f"{tool_base_url.rstrip('/')}/tool"},
            }
        )
    return {
        "name": name,
        "firstMessage": "Welcome to the drive thru, what can I get for you?",
        "model": {
            "provider": "openai",
            "model": "gpt-4o",
            "messages": [{"role": "system", "content": system}],
            "tools": tools,
        },
        "transcriber": {"provider": "deepgram", "model": "nova-2"},
        "voice": {"provider": "vapi", "voiceId": "Elliot"},
    }


def load_registry() -> dict[str, Any]:
    if os.path.exists(REGISTRY_PATH):
        with open(REGISTRY_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def save_registry(registry: Mapping[str, Any]) -> None:
    os.makedirs(os.path.dirname(REGISTRY_PATH) or ".", exist_ok=True)
    with open(REGISTRY_PATH, "w", encoding="utf-8") as fh:
        json.dump(registry, fh, indent=2, sort_keys=True)
