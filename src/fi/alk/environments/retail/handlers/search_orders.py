def handle(args, db):
    email = args.get("email")
    postal_code = args.get("postal_code")
    customer_id = args.get("customer_id")
    status = args.get("status")
    if not email and not customer_id:
        raise ToolError("Provide the customer's email (with postal_code) or customer_id.", category="invalid_input")
    if customer_id:
        cust = db.one("SELECT * FROM customers WHERE customer_id = ?", [customer_id])
    else:
        cust = db.one("SELECT * FROM customers WHERE lower(email) = ?", [str(email).strip().lower()])
    if not cust:
        raise ToolError("No customer matches those details.", category="not_found")
    if email and str(email).strip().lower() != cust["email"].strip().lower():
        raise ToolError("Email does not match the customer record.", category="permission_denied")
    if postal_code and str(postal_code).strip().lower() != cust["postal_code"].strip().lower():
        raise ToolError("Postal code does not match the customer record.", category="permission_denied")
    if not customer_id and not postal_code:
        raise ToolError("An email alone does not identify the customer; add the postal_code on file.", category="permission_denied")
    if status:
        rows = db.query("SELECT * FROM orders WHERE customer_id = ? AND status = ? ORDER BY order_date DESC", [cust["customer_id"], status])
    else:
        rows = db.query("SELECT * FROM orders WHERE customer_id = ? ORDER BY order_date DESC", [cust["customer_id"]])
    orders = []
    for o in rows:
        items = db.query("SELECT sku, quantity, returned_quantity FROM order_items WHERE order_id = ?", [o["order_id"]])
        orders.append({"order_id": o["order_id"], "order_date": o["order_date"], "delivery_date": o["delivery_date"], "status": o["status"], "total_net_cents": o["total_net_cents"], "currency": o["currency"], "items": [{"sku": i["sku"], "quantity": i["quantity"], "returned_quantity": i["returned_quantity"]} for i in items]})
    return {"customer_id": cust["customer_id"], "customer_name": cust["name"], "orders": orders, "count": len(orders)}
