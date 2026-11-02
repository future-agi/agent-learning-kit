"""Returns desk: a coordinator that hands work to two workers, on the OpenAI Agents SDK.

The coordinator never touches orders or refunds itself. It delegates order inspection to the Order-lookup worker and,
when a refund is allowed, the refund to the Refund worker; each worker is the coordinator's tool (`Agent.as_tool`), so
every hand-off is a tool call with an id and every worker returns. Each worker holds only its own tools: the Order-lookup
worker reads orders and refunds, the Refund worker lists and issues refunds. Issuing is idempotent per order: an order
that already has a refund gets that refund back, never a second one.

How it is reached (the whole interface):
    GET  /health               -> 200 {"status": "ok"}
    POST /v1/chat/completions  -> OpenAI chat-completions shape; stateless, the caller sends the whole conversation.
It listens on $PORT (default 8080).

Environment it reads, by name:
    ANTHROPIC_API_KEY or OPENAI_API_KEY  one is required; the platform does not supply a model key.
    RETURNS_MODEL      optional. Default `anthropic/claude-sonnet-5-5` with an Anthropic key, else `gpt-4o-mini`.
    OPENAI_BASE_URL    optional. A gateway for OpenAI model calls, read by the openai client.
    PORT               optional. Default 8080.
    OTEL_EXPORTER_OTLP_TRACES_ENDPOINT or OTEL_EXPORTER_OTLP_ENDPOINT
                       when present (the platform sets the first with the trace export), each turn's agent, hand-off
                       and tool spans are exported through traceAI's OpenAI Agents instrumentation.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from agents import Agent, Runner, function_tool, set_trace_processors

# --- the returns world: fixed in memory so every run sees the same facts -------------------------------------------

ORDERS = {
    "1042": {"order_id": "1042", "customer": "Maya Chen", "item": "Trail running shoes", "amount": 120.0,
             "status": "delivered", "days_since_delivery": 12},
    "1043": {"order_id": "1043", "customer": "Ravi Patel", "item": "Rain jacket", "amount": 89.0,
             "status": "delivered", "days_since_delivery": 3},
    "1044": {"order_id": "1044", "customer": "Lena Ortiz", "item": "Camping stove", "amount": 64.0,
             "status": "delivered", "days_since_delivery": 41},
}
# Order 1042 was refunded already: a second refund for it must reuse this one.
REFUNDS = {"1042": {"refund_id": "RF-7", "order_id": "1042", "amount": 120.0, "status": "issued"}}
RETURN_WINDOW_DAYS = 30
_LOCK = threading.Lock()


@function_tool
def get_order(order_id: str) -> str:
    """Look up one order by its id: customer, item, amount, status and days since delivery."""
    order = ORDERS.get(str(order_id).strip())
    return json.dumps(order) if order else json.dumps({"error": f"no order {order_id}"})


@function_tool
def list_refunds(order_id: str) -> str:
    """The refunds already issued for an order, if any."""
    refund = REFUNDS.get(str(order_id).strip())
    return json.dumps({"order_id": order_id, "refunds": [refund] if refund else []})


@function_tool
def issue_refund(order_id: str, amount: float) -> str:
    """Issue a refund for a delivered order inside the return window. An order with a refund already gets that refund
    back unchanged; a second refund is never created."""
    key = str(order_id).strip()
    with _LOCK:
        order = ORDERS.get(key)
        if order is None:
            return json.dumps({"error": f"no order {order_id}"})
        if key in REFUNDS:
            return json.dumps({"reused": True, **REFUNDS[key]})
        if order["days_since_delivery"] > RETURN_WINDOW_DAYS:
            return json.dumps({"error": "outside the 30-day return window"})
        if float(amount) > order["amount"]:
            return json.dumps({"error": "refund exceeds the order amount"})
        refund = {"refund_id": f"RF-{len(REFUNDS) + 7}", "order_id": key, "amount": float(amount), "status": "issued"}
        REFUNDS[key] = refund
        return json.dumps({"reused": False, **refund})


def _model() -> str:
    explicit = os.environ.get("RETURNS_MODEL", "").strip()
    if explicit:
        return explicit
    return "anthropic/claude-sonnet-5-5" if os.environ.get("ANTHROPIC_API_KEY") else "gpt-4o-mini"


def _model_ref() -> Any:
    name = _model()
    if "/" in name:
        # Non-OpenAI providers go through the SDK's LiteLLM model.
        from agents.extensions.models.litellm_model import LitellmModel

        return LitellmModel(model=name)
    return name


ORDER_LOOKUP = Agent(
    name="Order-lookup worker",
    instructions=(
        "You inspect returns. Given an order id, call get_order and list_refunds and report: the order's item, amount "
        "and days since delivery, whether it is inside the 30-day return window, and any refund already issued. Report "
        "facts only; you cannot issue refunds or change orders."
    ),
    tools=[get_order, list_refunds],
    model=_model_ref(),
)
REFUND = Agent(
    name="Refund worker",
    instructions=(
        "You issue refunds. Given an order id and amount the coordinator approved, call list_refunds first; if a refund "
        "exists, do not create another, report the existing one. Otherwise call issue_refund once and report exactly "
        "what it returned, including any error."
    ),
    tools=[list_refunds, issue_refund],
    model=_model_ref(),
)
COORDINATOR = Agent(
    name="Returns coordinator",
    instructions=(
        "You are the returns desk for Northwind Outdoor. You never look up orders or issue refunds yourself: ask the "
        "order lookup worker to inspect the order first, then, only if the order is inside the return window and has "
        "no refund yet, ask the refund worker to refund the full amount. Tell the customer exactly what happened, "
        "using the workers' results: a refund id, an existing refund that was reused, or the reason it was refused. "
        "Never claim a refund that a worker did not report."
    ),
    tools=[
        ORDER_LOOKUP.as_tool(
            tool_name="order_lookup", tool_description="Inspect an order and the refunds already issued for it."
        ),
        REFUND.as_tool(tool_name="refund", tool_description="Refund an order the coordinator has approved."),
    ],
    model=_model_ref(),
)


def _tracing() -> None:
    """Export agent, hand-off and tool spans when the platform asks for traces; nothing is sent otherwise."""
    if not (os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")):
        set_trace_processors([])
        return
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from traceai_openai_agents import OpenAIAgentsInstrumentor

    provider = TracerProvider(resource=Resource.create({"service.name": "returns-reference"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    # Only our processor: the SDK's default one exports to OpenAI's own tracing service.
    set_trace_processors([])
    OpenAIAgentsInstrumentor().instrument(tracer_provider=provider)


def reply(messages: list[dict[str, Any]]) -> str:
    conversation = [
        {"role": one.get("role", "user"), "content": str(one.get("content") or "")}
        for one in messages
        if one.get("role") in ("user", "assistant")
    ]
    result = asyncio.run(Runner.run(COORDINATOR, conversation))
    return str(result.final_output or "")


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - http.server's name
        if self.path.rstrip("/") == "/health":
            self._send(200, {"status": "ok"})
        else:
            self._send(404, {"error": "not found"})

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
            200,
            {"choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]},
        )

    def log_message(self, *_: Any) -> None:
        return


if __name__ == "__main__":
    _tracing()
    port = int(os.environ.get("PORT") or 8080)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
