"""A retail returns desk: the reference environment for external RL trainers.

Why this exists. Every other environment in this repo is authored from a customer's agent,
whose tools live inside that agent's own process. An external trainer never runs that process,
so those tools cannot act for it -- it would be told a refund succeeded while nothing changed.
Here we own the seam, so the tools are **module-level callables over the store**. The harness
compiles each into a world-side handler and runs it itself, which is the only shape a driver
outside the agent can actually use.

The three tools are the smallest set that makes a reward mean something:

* ``look_up_order`` reads an order. Harmless on its own.
* ``return_policy`` reads the rule the decision turns on.
* ``refund`` **changes a row**. Nothing else does.

That last point is the design. A refund is a row change, read back from the store by every
check, so a tool that merely answers "ok" earns nothing. The seeded world holds one order
inside the 30-day window and one far outside it, so the same task has a right answer in both
directions: refund the first, and refuse the second with an explanation. A correct refusal
writes nothing and must still pass.

``chat`` is here so the same environment can also be exercised the ordinary way, by an agent
under test, rather than only by a trainer. It is a deliberately literal rule-follower, not a
model: its job is to be a known-good baseline, not to be clever.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import psycopg

ORDER_PATTERN = re.compile(r"\bORD-[0-9]{4}\b", re.IGNORECASE)


def _connect() -> psycopg.Connection:
    return psycopg.connect(os.environ["DATABASE_URL"])


def _row(connection: psycopg.Connection, order_id: str) -> dict[str, Any] | None:
    found = connection.execute(
        "SELECT order_id, customer_name, item, amount, delivered_on, refunded, refund_reference"
        " FROM orders WHERE order_id = %s",
        (order_id,),
    ).fetchone()
    if found is None:
        return None
    keys = (
        "order_id",
        "customer_name",
        "item",
        "amount",
        "delivered_on",
        "refunded",
        "refund_reference",
    )
    record = dict(zip(keys, found))
    record["amount"] = float(record["amount"])
    record["delivered_on"] = record["delivered_on"].isoformat()
    return record


# --- tools -----------------------------------------------------------------------------
# Module-level and side-effect-honest: each does exactly what its name says to the store, so
# a check can read the store back and disagree with a tool that lied.


def look_up_order(order_id: str) -> dict[str, Any]:
    """One order, or a not-found marker. Reads only.

    ``days_since_delivery`` is derived here rather than left to the caller, because a policy
    decision that depends on the caller's clock is not reproducible.
    """
    with _connect() as connection:
        record = _row(connection, str(order_id).upper())
    if record is None:
        return {"found": False, "order_id": order_id}
    delivered = date.fromisoformat(record["delivered_on"])
    return {
        "found": True,
        **record,
        "days_since_delivery": (date.today() - delivered).days,
    }


def return_policy() -> dict[str, Any]:
    """The returns rule, as the world holds it. Reads only."""
    with _connect() as connection:
        found = connection.execute(
            "SELECT window_days, text FROM policy WHERE name = 'returns'"
        ).fetchone()
    if found is None:
        return {"found": False}
    return {"found": True, "window_days": int(found[0]), "text": str(found[1])}


def refund(order_id: str) -> dict[str, Any]:
    """Refund one order. The only tool that changes anything.

    Refuses rather than raising, because a refusal is a legitimate outcome a trainer must be
    able to observe and explain. Refunding twice is reported as already refunded and leaves the
    original reference intact, so a repeated call cannot look like a second refund.
    """
    key = str(order_id).upper()
    with _connect() as connection:
        record = _row(connection, key)
        if record is None:
            return {"refunded": False, "reason": "unknown_order", "order_id": order_id}
        if record["refunded"]:
            return {
                "refunded": False,
                "reason": "already_refunded",
                "order_id": key,
                "refund_reference": record["refund_reference"],
            }
        reference = f"RF-{uuid.uuid4().hex[:10].upper()}"
        connection.execute(
            "UPDATE orders SET refunded = true, refund_reference = %s WHERE order_id = %s",
            (reference, key),
        )
        connection.commit()
    return {"refunded": True, "order_id": key, "refund_reference": reference}


TOOLS = {"look_up_order": look_up_order, "return_policy": return_policy, "refund": refund}


# --- the baseline agent ----------------------------------------------------------------


def _reply(text: str) -> str:
    """A literal rule-follower: look the order up, read the policy, then refund or explain.

    Not a model, and not trying to be. It exists so this environment has a known-good baseline
    an authored suite can be checked against before any trainer touches it.
    """
    match = ORDER_PATTERN.search(text)
    if match is None:
        return "Could you give me the order number? It looks like ORD-0000."

    order = look_up_order(match.group(0))
    if not order["found"]:
        return f"I cannot find order {match.group(0)}."

    policy = return_policy()
    window = policy.get("window_days", 30) if policy.get("found") else 30
    if order["refunded"]:
        return (
            f"Order {order['order_id']} was already refunded"
            f" (reference {order['refund_reference']})."
        )
    if order["days_since_delivery"] > window:
        return (
            f"Order {order['order_id']} was delivered {order['days_since_delivery']} days ago,"
            f" which is outside our {window}-day returns window, so I cannot refund it."
        )
    done = refund(order["order_id"])
    if not done["refunded"]:
        return f"I could not refund {order['order_id']}: {done['reason']}."
    return (
        f"I have refunded {order['amount']:.2f} for order {order['order_id']}."
        f" Your reference is {done['refund_reference']}."
    )


class Handler(BaseHTTPRequestHandler):
    """An OpenAI-compatible chat endpoint, plus a health check."""

    protocol_version = "HTTP/1.1"

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's name
        if self.path.rstrip("/") == "/health":
            self._send(200, {"status": "ok"})
            return
        self._send(404, {"detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's name
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._send(404, {"detail": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            request = json.loads(self.rfile.read(length) or b"{}")
            messages = request.get("messages") or []
            last = next(
                (
                    str(message.get("content") or "")
                    for message in reversed(messages)
                    if message.get("role") == "user"
                ),
                "",
            )
        except (ValueError, TypeError) as exc:
            self._send(400, {"detail": f"bad request: {exc}"})
            return
        try:
            answer = _reply(last)
        except Exception as exc:  # noqa: BLE001 - the target reports its own failure
            self._send(500, {"detail": f"{type(exc).__name__}: {exc}"})
            return
        self._send(
            200,
            {
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "model": request.get("model") or "retail-returns-baseline",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": answer},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    def log_message(self, *_args: Any) -> None:
        """Quiet: the harness keeps its own record of every call."""


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
