def handle(args, db):
    customer_id = args.get("customer_id")
    email = args.get("email")
    postal_code = args.get("postal_code")
    if not customer_id:
        raise ToolError("customer_id is required.", category="invalid_input")
    cust = db.one("SELECT * FROM customers WHERE customer_id = ?", [customer_id])
    if not cust:
        raise ToolError(f"Customer {customer_id} not found.", category="not_found")
    if not (email or postal_code):
        raise ToolError("Verify the customer with their email or postal_code before disclosing the balance.", category="invalid_input")
    if email and str(email).strip().lower() != cust["email"].strip().lower():
        raise ToolError("Email does not match the customer record.", category="permission_denied")
    if postal_code and str(postal_code).strip().lower() != cust["postal_code"].strip().lower():
        raise ToolError("Postal code does not match the customer record.", category="permission_denied")
    credits = db.query("SELECT * FROM store_credits WHERE customer_id = ? ORDER BY issued_at", [customer_id])
    return {"customer_id": customer_id, "balance_cents": int(cust["store_credit_cents"] or 0), "currency": "USD", "credits": [{"credit_id": c["credit_id"], "amount_cents": c["amount_cents"], "source": c["source"], "issued_at": c["issued_at"], "expires_at": c["expires_at"]} for c in credits]}
