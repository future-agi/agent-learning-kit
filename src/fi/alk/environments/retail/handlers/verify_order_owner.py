def _caller(db):
    """The customer signed in to this conversation. The session sets it; an ID, email or name the
    desk is told never changes who it is talking to."""
    row = db.one("SELECT customer_id FROM session_caller WHERE id = 'default'")
    return row["customer_id"] if row else None


def handle(args, db):
    order_id = args.get("order_id")
    postal_code = args.get("postal_code")
    if not order_id:
        raise ToolError("order_id is required.", category="invalid_input")
    # The order number and the postal code on file together are the secret; an email or a customer
    # ID alone is something anyone may know, so it never verifies.
    if not postal_code:
        raise ToolError("postal_code is required: the order number and the postal code on file verify the customer.", category="invalid_input")
    order = db.one("SELECT * FROM orders WHERE order_id = ?", [order_id])
    if not order:
        raise ToolError(f"Order {order_id} not found.", category="not_found")
    customer = db.one("SELECT * FROM customers WHERE customer_id = ?", [order["customer_id"]])
    if not customer:
        raise ToolError(f"Customer for order {order_id} not found.", category="not_found")

    email = args.get("email")
    cust_id = args.get("customer_id")
    if str(postal_code).strip().lower() != customer["postal_code"].strip().lower():
        raise ToolError("Postal code does not match order record.", category="permission_denied")
    if email and email.strip().lower() != customer["email"].strip().lower():
        raise ToolError("Email does not match order record.", category="permission_denied")
    if cust_id and cust_id.strip() != customer["customer_id"].strip():
        raise ToolError("Customer ID does not match order record.", category="permission_denied")
    # Knowing another person's order number and postal code does not make the caller that person.
    if customer["customer_id"] != _caller(db):
        raise ToolError(f"Order {order_id} is not an order of the customer signed in to this conversation.", category="permission_denied")

    return {
        "verified": True,
        "order_id": order_id,
        "customer_id": customer["customer_id"],
        "customer_name": customer["name"]
    }
