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
    customer_id = args.get("customer_id")
    email = args.get("email")
    postal_code = args.get("postal_code")
    if not customer_id:
        raise ToolError("customer_id is required.", category="invalid_input")
    cust = db.one("SELECT * FROM customers WHERE customer_id = ?", [customer_id])
    if not cust:
        raise ToolError(f"Customer {customer_id} not found.", category="not_found")
    # The postal code on file is the secret; an email is something anyone may know.
    if not postal_code:
        raise ToolError("Verify the customer with the postal code on file before disclosing the balance.", category="invalid_input")
    if email and str(email).strip().lower() != cust["email"].strip().lower():
        raise ToolError("Email does not match the customer record.", category="permission_denied")
    if str(postal_code).strip().lower() != cust["postal_code"].strip().lower():
        raise ToolError("Postal code does not match the customer record.", category="permission_denied")
    _for_caller(db, customer_id, f"the store credit of {customer_id}")
    credits = db.query("SELECT * FROM store_credits WHERE customer_id = ? ORDER BY issued_at", [customer_id])
    return {"customer_id": customer_id, "balance_cents": int(cust["store_credit_cents"] or 0), "currency": "USD", "credits": [{"credit_id": c["credit_id"], "amount_cents": c["amount_cents"], "source": c["source"], "issued_at": c["issued_at"], "expires_at": c["expires_at"]} for c in credits]}
