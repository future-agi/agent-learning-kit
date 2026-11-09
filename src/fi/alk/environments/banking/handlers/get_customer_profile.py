def handle(args, db):
    customer_id = args.get("customer_id")
    session_id = args.get("session_id")
    if not customer_id:
        raise ToolError("Missing customer_id.")
    if not session_id:
        raise ToolError("Missing session_id.")
    sess = db.one("SELECT * FROM sessions WHERE session_id = ?", [session_id])
    if not sess:
        raise ToolError(f"Session {session_id} not found.")
    if sess["customer_id"] != customer_id:
        raise ToolError("Session customer mismatch.")
    if not sess["is_verified"]:
        raise ToolError("Authorization required: customer session is not verified.")
    cust = db.one("SELECT * FROM customers WHERE customer_id = ?", [customer_id])
    if not cust:
        raise ToolError(f"Customer {customer_id} not found.")
    accounts = db.query("SELECT account_id FROM accounts WHERE customer_id = ?", [customer_id])
    cards = db.query("SELECT card_id FROM cards WHERE customer_id = ?", [customer_id])
    return {
        "customer_id": customer_id,
        "first_name": cust["first_name"],
        "last_name": cust["last_name"],
        "email": cust["email"],
        "phone": cust["phone"],
        "address": cust["address"] or "",
        "customer_since": cust["customer_since"] or "",
        "accounts_count": len(accounts),
        "cards_count": len(cards),
        "step_up_verified": bool(sess["step_up_at"]),
    }
