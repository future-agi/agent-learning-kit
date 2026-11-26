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
    new_address = args.get("new_address")
    confirmed = args.get("confirmed")
    request_key = args.get("request_key")
    if not order_id or not customer_id:
        raise ToolError("order_id and customer_id are required.", category="invalid_input")
    if not isinstance(new_address, str) or len(new_address.strip()) < 8:
        raise ToolError("new_address must be a full postal address.", category="invalid_input")
    if confirmed is None:
        raise ToolError("confirmed is required.", category="invalid_input")
    if not confirmed:
        raise ToolError("Policy: the customer must confirm the new address first.", category="policy_blocked")
    order = db.one("SELECT * FROM orders WHERE order_id = ?", [order_id])
    if not order:
        raise ToolError(f"Order {order_id} not found.", category="not_found")
    if order["customer_id"].strip() != str(customer_id).strip():
        raise ToolError(f"Customer {customer_id} does not match order owner.", category="permission_denied")
    _for_caller(db, customer_id, f"order {order_id}")
    clock = db.one("SELECT now FROM clock")["now"]
    if request_key:
        existing = db.one("SELECT * FROM address_changes WHERE request_key = ?", [request_key])
        if existing:
            # A replay is the same address for the same order and customer; anything else under the
            # key would report a change that was never made.
            if existing["order_id"] != order_id or existing["customer_id"] != customer_id or existing["new_address"] != new_address.strip():
                raise ToolError(f"request_key {request_key!r} was already used for a different address change.", category="conflict")
            return {"change_id": existing["change_id"], "order_id": order_id, "old_address": existing["old_address"], "new_address": existing["new_address"], "idempotent": True}
    if order["status"] != "processing":
        raise ToolError(f"Policy: order {order_id} is {order['status']}; the address can only change while the order is processing.", category="policy_blocked")
    count = db.one("SELECT COUNT(*) AS c FROM address_changes")["c"]
    change_id = f"ADR{count + 1:02d}"
    old = order["shipping_address"] or ""
    db.execute("INSERT INTO address_changes (change_id, order_id, customer_id, old_address, new_address, request_key, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", [change_id, order_id, customer_id, old, new_address.strip(), request_key, clock])
    db.execute("UPDATE orders SET shipping_address = ? WHERE order_id = ?", [new_address.strip(), order_id])
    return {"change_id": change_id, "order_id": order_id, "old_address": old, "new_address": new_address.strip(), "idempotent": False}
