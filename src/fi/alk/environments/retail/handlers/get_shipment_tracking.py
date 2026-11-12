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
    if not order_id or not customer_id:
        raise ToolError("order_id and customer_id are required.", category="invalid_input")
    order = db.one("SELECT * FROM orders WHERE order_id = ?", [order_id])
    if not order:
        raise ToolError(f"Order {order_id} not found.", category="not_found")
    if order["customer_id"].strip() != str(customer_id).strip():
        raise ToolError(f"Customer {customer_id} does not match order owner.", category="permission_denied")
    _for_caller(db, customer_id, f"order {order_id}")
    shp = db.one("SELECT * FROM shipments WHERE order_id = ?", [order_id])
    if not shp:
        raise ToolError(f"No shipment record for order {order_id}.", category="not_found")
    return {"order_id": order_id, "order_status": order["status"], "shipment_id": shp["shipment_id"], "carrier": shp["carrier"], "tracking_number": shp["tracking_number"], "status": shp["status"], "shipped_at": shp["shipped_at"], "delivered_at": shp["delivered_at"], "last_event": shp["last_event"], "last_event_at": shp["last_event_at"], "shipping_address": order["shipping_address"] or ""}
