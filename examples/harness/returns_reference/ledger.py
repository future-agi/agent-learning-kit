"""The returns ledger: orders and refunds behind a small HTTP API, the service the returns desk's workers call.

    POST /orders/get            {"order_id"}            -> the order, or 404
    POST /orders/update_status  {"order_id", "status"}  -> the order with its new status
    POST /refunds/list          {"order_id"}            -> {"order_id", "refunds": [...]}
    POST /refunds/issue         {"order_id", "amount"}  -> the refund; an order with one gets it back unchanged
    GET  /health                                         -> 200 {"status": "ok"}

Issuing is idempotent per order: a second refund is never created. It listens on $LEDGER_PORT (default 8090).
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

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


def _order(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    order = ORDERS.get(str(body.get("order_id", "")).strip())
    return (200, order) if order else (404, {"error": f"no order {body.get('order_id')}"})


def _update_status(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    with _LOCK:
        order = ORDERS.get(str(body.get("order_id", "")).strip())
        if order is None:
            return 404, {"error": f"no order {body.get('order_id')}"}
        order["status"] = str(body.get("status") or order["status"])
        return 200, order


def _refunds(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    key = str(body.get("order_id", "")).strip()
    refund = REFUNDS.get(key)
    return 200, {"order_id": key, "refunds": [refund] if refund else []}


def _issue(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    key = str(body.get("order_id", "")).strip()
    with _LOCK:
        order = ORDERS.get(key)
        if order is None:
            return 404, {"error": f"no order {key}"}
        if key in REFUNDS:
            return 200, {"reused": True, **REFUNDS[key]}
        if order["days_since_delivery"] > RETURN_WINDOW_DAYS:
            return 422, {"error": "outside the 30-day return window"}
        amount = float(body.get("amount") or 0)
        if amount <= 0 or amount > order["amount"]:
            return 422, {"error": "the refund must be more than 0 and at most the order amount"}
        refund = {"refund_id": f"RF-{len(REFUNDS) + 7}", "order_id": key, "amount": amount, "status": "issued"}
        REFUNDS[key] = refund
        return 201, {"reused": False, **refund}


ROUTES = {"/orders/get": _order, "/orders/update_status": _update_status, "/refunds/list": _refunds,
          "/refunds/issue": _issue}


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
        route = ROUTES.get(self.path.rstrip("/"))
        if route is None:
            self._send(404, {"error": "not found"})
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            self._send(400, {"error": "the body is not JSON"})
            return
        self._send(*route(body if isinstance(body, dict) else {}))

    def log_message(self, *_: Any) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("LEDGER_PORT") or 8090)), Handler).serve_forever()
