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
    email = args.get("email")
    postal_code = args.get("postal_code")
    customer_id = args.get("customer_id")
    status = args.get("status")
    if not email and not customer_id:
        raise ToolError("Provide the customer's email or customer_id, with the postal_code on file.", category="invalid_input")
    # The postal code on file is the secret on either path: an email or a customer ID alone is
    # something anyone may know.
    if not postal_code:
        raise ToolError("The postal_code on file is required to identify the customer.", category="permission_denied")
    if customer_id:
        cust = db.one("SELECT * FROM customers WHERE customer_id = ?", [customer_id])
    else:
        cust = db.one("SELECT * FROM customers WHERE lower(email) = ?", [str(email).strip().lower()])
    if not cust:
        raise ToolError("No customer matches those details.", category="not_found")
    if email and str(email).strip().lower() != cust["email"].strip().lower():
        raise ToolError("Email does not match the customer record.", category="permission_denied")
    if str(postal_code).strip().lower() != cust["postal_code"].strip().lower():
        raise ToolError("Postal code does not match the customer record.", category="permission_denied")
    _for_caller(db, cust["customer_id"], f"the orders of {cust['customer_id']}")
    if status:
        rows = db.query("SELECT * FROM orders WHERE customer_id = ? AND status = ? ORDER BY order_date DESC", [cust["customer_id"], status])
    else:
        rows = db.query("SELECT * FROM orders WHERE customer_id = ? ORDER BY order_date DESC", [cust["customer_id"]])
    orders = []
    for o in rows:
        items = db.query("SELECT sku, quantity, returned_quantity FROM order_items WHERE order_id = ?", [o["order_id"]])
        orders.append({"order_id": o["order_id"], "order_date": o["order_date"], "delivery_date": o["delivery_date"], "status": o["status"], "total_net_cents": o["total_net_cents"], "currency": o["currency"], "items": [{"sku": i["sku"], "quantity": i["quantity"], "returned_quantity": i["returned_quantity"]} for i in items]})
    return {"customer_id": cust["customer_id"], "customer_name": cust["name"], "orders": orders, "count": len(orders)}
