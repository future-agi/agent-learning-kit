def _caller(db):
    """The customer signed in to this conversation. The session sets it; an ID, email or name the
    desk is told never changes who it is talking to."""
    row = db.one("SELECT customer_id FROM session_caller WHERE id = 'default'")
    return row["customer_id"] if row else None


def _for_caller(db, customer_id, what):
    """Act only for the signed-in customer: a customer_id argument is checked, never trusted."""
    caller = _caller(db)
    if not caller or str(customer_id or "").strip() != caller:
        raise ToolError(f"Permission denied: {what} is not the signed-in customer's ({caller or 'nobody is signed in'}).", category="permission_denied")


def handle(args, db):
    order_id = args.get("order_id")
    customer_id = args.get("customer_id")
    reason = args.get("reason")
    confirmed = args.get("confirmed")
    request_key = args.get("request_key")
    if not order_id or not customer_id:
        raise ToolError("order_id and customer_id are required.", category="invalid_input")
    if not reason:
        raise ToolError("reason is required.", category="invalid_input")
    if confirmed is None:
        raise ToolError("confirmed is required.", category="invalid_input")
    if not confirmed:
        raise ToolError("Policy: the customer must confirm the cancellation first.", category="policy_blocked")
    order = db.one("SELECT * FROM orders WHERE order_id = ?", [order_id])
    if not order:
        raise ToolError(f"Order {order_id} not found.", category="not_found")
    if order["customer_id"].strip() != str(customer_id).strip():
        raise ToolError(f"Customer {customer_id} does not match order owner.", category="permission_denied")
    _for_caller(db, customer_id, f"order {order_id}")
    clock = db.one("SELECT now FROM clock")["now"]
    if request_key:
        existing = db.one("SELECT * FROM cancellations WHERE request_key = ?", [request_key])
        if existing:
            # A replay is the same order for the same customer; a key reused for another order would
            # report a cancellation that never happened.
            if existing["order_id"] != order_id or existing["customer_id"] != customer_id:
                raise ToolError(f"request_key {request_key!r} was already used to cancel a different order.", category="conflict")
            return {"cancellation_id": existing["cancellation_id"], "order_id": order_id, "status": "cancelled", "idempotent": True, "cancelled_at": existing["created_at"]}
    if order["status"] == "cancelled":
        existing = db.one("SELECT * FROM cancellations WHERE order_id = ?", [order_id])
        return {"cancellation_id": existing["cancellation_id"] if existing else None, "order_id": order_id, "status": "cancelled", "idempotent": True, "cancelled_at": order["cancelled_at"]}
    if order["status"] != "processing":
        raise ToolError(f"Policy: order {order_id} is {order['status']} and can no longer be cancelled; it can be returned after delivery.", category="policy_blocked")
    count = db.one("SELECT COUNT(*) AS c FROM cancellations")["c"]
    cancellation_id = f"CAN{count + 1:02d}"
    db.execute("INSERT INTO cancellations (cancellation_id, order_id, customer_id, reason, request_key, created_at) VALUES (?, ?, ?, ?, ?, ?)", [cancellation_id, order_id, customer_id, reason, request_key, clock])
    db.execute("UPDATE orders SET status = 'cancelled', cancelled_at = ? WHERE order_id = ?", [clock, order_id])
    db.execute("UPDATE shipments SET status = 'cancelled', last_event = 'Order cancelled before dispatch', last_event_at = ? WHERE order_id = ?", [clock, order_id])
    return {"cancellation_id": cancellation_id, "order_id": order_id, "status": "cancelled", "idempotent": False, "cancelled_at": clock, "refund_note": "The payment authorization is released; nothing was charged for a cancelled processing order."}
