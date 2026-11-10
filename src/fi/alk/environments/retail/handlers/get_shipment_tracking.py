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
    shp = db.one("SELECT * FROM shipments WHERE order_id = ?", [order_id])
    if not shp:
        raise ToolError(f"No shipment record for order {order_id}.", category="not_found")
    return {"order_id": order_id, "order_status": order["status"], "shipment_id": shp["shipment_id"], "carrier": shp["carrier"], "tracking_number": shp["tracking_number"], "status": shp["status"], "shipped_at": shp["shipped_at"], "delivered_at": shp["delivered_at"], "last_event": shp["last_event"], "last_event_at": shp["last_event_at"], "shipping_address": order["shipping_address"] or ""}
