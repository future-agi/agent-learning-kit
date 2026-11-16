"""The returns ledger: orders and refunds behind a small HTTP API, the service the returns desk's workers call.

    POST /orders/get            {"order_id"}            -> the order, or 404
    POST /orders/update_status  {"order_id", "status"}  -> the order with its new status
    POST /refunds/list          {"order_id"}            -> {"order_id", "refunds": [...]}
    POST /refunds/issue         {"order_id", "amount"}  -> a new refund
    GET  /health                                         -> 200 {"status": "ok"}

Issuing always creates a refund; checking for an existing one is the caller's job. A record's `fault` names the
deliberate defect a negative control exercises on it, and every answer about that order carries it. It listens on
--port, else $LEDGER_PORT (8090).
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
    "1045": {"order_id": "1045", "customer": "Sam Okafor", "item": "Head torch", "amount": 35.0,
             "status": "delivered", "days_since_delivery": 5, "fault": "payment_times_out"},
    "1046": {"order_id": "1046", "customer": "Ana Silva", "item": "Sleeping mat", "amount": 52.0,
             "status": "delivered", "days_since_delivery": 6, "fault": "refund_never_returns"},
    "1047": {"order_id": "1047", "customer": "Tom Berg", "item": "Water filter", "amount": 41.0,
             "status": "delivered", "days_since_delivery": 8, "fault": "skip_refund_check"},
    "1048": {"order_id": "1048", "customer": "Ida Novak", "item": "Trekking poles", "amount": 75.0,
             "status": "delivered", "days_since_delivery": 2, "fault": "bypass_channel"},
}
# Orders 1042 and 1047 were refunded already. Issuing always creates a refund; checking for one is the caller's job.
REFUNDS: dict[str, list[dict[str, Any]]] = {
    "1042": [{"refund_id": "RF-7", "order_id": "1042", "amount": 120.0, "status": "issued"}],
    "1047": [{"refund_id": "RF-8", "order_id": "1047", "amount": 41.0, "status": "issued"}],
}
# The payment service never takes a refund for a record with one of these defects: the answer is an error.
PAYMENT_FAULTS = {
    "payment_times_out": "payment service timed out",
    "refund_never_returns": "payment service did not answer",
}
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


def _marked(key: str, answer: dict[str, Any]) -> dict[str, Any]:
    fault = ORDERS.get(key, {}).get("fault")
    return {**answer, "fault": fault} if fault else answer


def _refunds(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    key = str(body.get("order_id", "")).strip()
    return 200, _marked(key, {"order_id": key, "refunds": list(REFUNDS.get(key, []))})


def _issue(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    key = str(body.get("order_id", "")).strip()
    with _LOCK:
        order = ORDERS.get(key)
        if order is None:
            return 404, {"error": f"no order {key}"}
        if order["days_since_delivery"] > RETURN_WINDOW_DAYS:
            return 422, {"error": "outside the 30-day return window"}
        amount = float(body.get("amount") or 0)
        if amount <= 0 or amount > order["amount"]:
            return 422, {"error": "the refund must be more than 0 and at most the order amount"}
        if order.get("fault") in PAYMENT_FAULTS:
            return 504, _marked(key, {"error": PAYMENT_FAULTS[order["fault"]]})
        issued = sum(len(refunds) for refunds in REFUNDS.values())
        refund = {"refund_id": f"RF-{issued + 7}", "order_id": key, "amount": amount, "status": "issued"}
        REFUNDS.setdefault(key, []).append(refund)
        return 201, _marked(key, dict(refund))


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
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("LEDGER_PORT") or 8090))
    ThreadingHTTPServer(("0.0.0.0", parser.parse_args().port), Handler).serve_forever()
