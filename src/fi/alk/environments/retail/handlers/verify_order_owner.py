def handle(args, db):
    order_id = args.get("order_id")
    if not order_id:
        raise ToolError("order_id is required.", category="invalid_input")
    order = db.one("SELECT * FROM orders WHERE order_id = ?", [order_id])
    if not order:
        raise ToolError(f"Order {order_id} not found.", category="not_found")
    customer = db.one("SELECT * FROM customers WHERE customer_id = ?", [order["customer_id"]])
    if not customer:
        raise ToolError(f"Customer for order {order_id} not found.", category="not_found")
    
    email = args.get("email")
    postal_code = args.get("postal_code")
    cust_id = args.get("customer_id")
    
    if not (email or postal_code or cust_id):
        raise ToolError("At least one verification detail (email, postal_code, or customer_id) must be provided.", category="invalid_input")
    
    if email and email.strip().lower() != customer["email"].strip().lower():
        raise ToolError("Email does not match order record.", category="permission_denied")
    if postal_code and postal_code.strip().lower() != customer["postal_code"].strip().lower():
        raise ToolError("Postal code does not match order record.", category="permission_denied")
    if cust_id and cust_id.strip() != customer["customer_id"].strip():
        raise ToolError("Customer ID does not match order record.", category="permission_denied")
        
    return {
        "verified": True,
        "order_id": order_id,
        "customer_id": customer["customer_id"],
        "customer_name": customer["name"]
    }
