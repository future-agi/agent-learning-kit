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
    clock = db.one("SELECT now FROM clock")["now"]
    if request_key:
        existing = db.one("SELECT * FROM address_changes WHERE request_key = ?", [request_key])
        if existing:
            return {"change_id": existing["change_id"], "order_id": order_id, "old_address": existing["old_address"], "new_address": existing["new_address"], "idempotent": True}
    if order["status"] != "processing":
        raise ToolError(f"Policy: order {order_id} is {order['status']}; the address can only change while the order is processing.", category="policy_blocked")
    count = db.one("SELECT COUNT(*) AS c FROM address_changes")["c"]
    change_id = f"ADR{count + 1:02d}"
    old = order["shipping_address"] or ""
    db.execute("INSERT INTO address_changes (change_id, order_id, customer_id, old_address, new_address, request_key, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", [change_id, order_id, customer_id, old, new_address.strip(), request_key, clock])
    db.execute("UPDATE orders SET shipping_address = ? WHERE order_id = ?", [new_address.strip(), order_id])
    return {"change_id": change_id, "order_id": order_id, "old_address": old, "new_address": new_address.strip(), "idempotent": False}
