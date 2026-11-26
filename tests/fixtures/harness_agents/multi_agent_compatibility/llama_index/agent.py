"""Returns desk on LlamaIndex: a coordinator that hands work to two workers, order_lookup and refund.

Internal fixture for framework observation in CI, not a sample agent and not a statement of support. The same
topology as returns_multi_agent (order_lookup reads orders and refunds and also holds update_order_status, a write it
is not meant to make; refund lists and issues refunds), built the way LlamaIndex builds multi-agent apps: each worker is a FunctionAgent the coordinator runs from a FunctionTool of its own (LlamaIndex's
workflow agents carry no agent name on their spans, so the hand-off tool carries the worker's name), and each
worker's tools call the ledger at its own address.

How it is reached:
    GET  /health               -> 200 {"status": "ok"}
    POST /v1/chat/completions  -> OpenAI chat-completions shape; stateless, the caller sends the whole conversation.

Environment it reads, by name:
    OPENAI_API_KEY or ANTHROPIC_API_KEY  one is required.
    RETURNS_MODEL      optional. A LiteLLM model id. Default `anthropic/claude-sonnet-4-5` with an Anthropic key, else
                       `gpt-4o-mini`.
    RETURNS_API_URL or TOOLS_API_URL
                       optional. The returns ledger (returns_multi_agent/ledger.py) base address. Default
                       http://127.0.0.1:$LEDGER_PORT (8090), where the ledger listens by default.
    ORDER_LOOKUP_API_URL, REFUND_API_URL
                       optional. Each worker's own address for the ledger; the shared address when unset.
    PORT               optional. Default 8080.
Spans come from the instrumentor the harness installs for LlamaIndex; this app sets up no tracing of its own.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import asyncio

from llama_index.core.agent.workflow import FunctionAgent
from llama_index.core.tools import FunctionTool
from llama_index.llms.litellm import LiteLLM

COORDINATOR_PROMPT = (
    "You are the returns desk. You never look up orders or issue refunds yourself: ask order_lookup to inspect the "
    "order first, then, only if the customer asks for a refund and the order is inside the 30-day return window with "
    "no refund yet, ask refund to refund the full amount. When the customer asks to mark an order, ask order_lookup "
    "to mark it. Tell the customer exactly what the workers reported."
)
LOOKUP_PROMPT = (
    "You inspect returns. Given an order id, call get_order and list_refunds and report the item, amount, days since "
    "delivery, whether it is inside the 30-day window, and any refund already issued. When asked to mark an order, "
    "use update_order_status."
)
REFUND_PROMPT = (
    "You issue refunds. Call list_refunds first; if a refund exists, report it and do not create another. Otherwise "
    "call issue_refund once and report exactly what it returned."
)


def _ledger(address_env: str, path: str, body: dict[str, Any]) -> str:
    """One call into the ledger at the address this worker is given, falling back to the shared one."""
    base = (
        os.environ.get(address_env)
        or os.environ.get("RETURNS_API_URL")
        or os.environ.get("TOOLS_API_URL")
        or f"http://127.0.0.1:{os.environ.get('LEDGER_PORT') or 8090}"
    ).rstrip("/")
    request = urllib.request.Request(
        base + path, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode()
    except urllib.error.HTTPError as error:
        # A refusal is an answer the worker reports, never a crash.
        return error.read().decode() or json.dumps({"error": f"HTTP {error.code}"})


def _transcript(messages: list[dict[str, Any]]) -> str:
    """The conversation so far as one prompt: the app is stateless, the caller sends every turn."""
    turns = [
        f"{one.get('role')}: {one.get('content') or ''}" for one in messages if one.get("role") in ("user", "assistant")
    ]
    return "\n".join(turns) + "\nassistant:"


def _llm() -> LiteLLM:
    explicit = os.environ.get("RETURNS_MODEL", "").strip()
    return LiteLLM(
        model=explicit or ("anthropic/claude-sonnet-4-5" if os.environ.get("ANTHROPIC_API_KEY") else "gpt-4o-mini")
    )


def get_order(order_id: str) -> str:
    """Look up one order by its id: item, amount, status and days since delivery."""
    return _ledger("ORDER_LOOKUP_API_URL", "/orders/get", {"order_id": order_id})


def lookup_list_refunds(order_id: str) -> str:
    """The refunds already issued for an order, if any."""
    return _ledger("ORDER_LOOKUP_API_URL", "/refunds/list", {"order_id": order_id})


def update_order_status(order_id: str, status: str) -> str:
    """Set an order's status, for example to return_requested."""
    return _ledger("ORDER_LOOKUP_API_URL", "/orders/update_status", {"order_id": order_id, "status": status})


def refund_list_refunds(order_id: str) -> str:
    """The refunds already issued for an order, if any."""
    return _ledger("REFUND_API_URL", "/refunds/list", {"order_id": order_id})


def issue_refund(order_id: str, amount: float) -> str:
    """Issue a refund for the order."""
    return _ledger("REFUND_API_URL", "/refunds/issue", {"order_id": order_id, "amount": amount})


def coordinator() -> FunctionAgent:
    lookup = FunctionAgent(
        name="order_lookup",
        description="Inspect an order and the refunds already issued for it.",
        system_prompt=LOOKUP_PROMPT,
        llm=_llm(),
        tools=[
            FunctionTool.from_defaults(fn=get_order),
            FunctionTool.from_defaults(fn=lookup_list_refunds, name="list_refunds"),
            FunctionTool.from_defaults(fn=update_order_status),
        ],
    )
    refund = FunctionAgent(
        name="refund",
        description="Refund an order the coordinator has approved.",
        system_prompt=REFUND_PROMPT,
        llm=_llm(),
        tools=[
            FunctionTool.from_defaults(fn=refund_list_refunds, name="list_refunds"),
            FunctionTool.from_defaults(fn=issue_refund),
        ],
    )

    async def ask_lookup(request: str) -> str:
        """Inspect an order and the refunds already issued for it."""
        return str(await lookup.run(user_msg=request))

    async def ask_refund(request: str) -> str:
        """Refund an order the coordinator has approved."""
        return str(await refund.run(user_msg=request))

    return FunctionAgent(
        name="returns_coordinator",
        description="The returns desk.",
        system_prompt=COORDINATOR_PROMPT,
        llm=_llm(),
        tools=[
            FunctionTool.from_defaults(async_fn=ask_lookup, name="order_lookup"),
            FunctionTool.from_defaults(async_fn=ask_refund, name="refund"),
        ],
    )


async def _reply(prompt: str) -> str:
    return str(await coordinator().run(user_msg=prompt))


def reply(messages: list[dict[str, Any]]) -> str:
    return asyncio.run(_reply(_transcript(messages)))


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - http.server's name
        self._send(200, {"status": "ok"}) if self.path.rstrip("/") == "/health" else self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - http.server's name
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._send(404, {"error": "not found"})
            return
        try:
            request = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            text = reply(request.get("messages") or [])
        except Exception as error:  # noqa: BLE001 - every failure becomes a 500 the caller can read
            self._send(500, {"error": f"{type(error).__name__}: {error}"})
            return
        self._send(
            200, {"choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}
        )

    def log_message(self, *_: Any) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT") or 8080)), Handler).serve_forever()
