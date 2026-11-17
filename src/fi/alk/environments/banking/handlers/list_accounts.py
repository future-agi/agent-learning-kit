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
    rows = db.query("SELECT * FROM accounts WHERE customer_id = ? ORDER BY account_id", [customer_id])
    accounts = [
        {
            "account_id": a["account_id"],
            "account_number_masked": "••••••" + a["account_number"][-4:],
            "account_type": a["account_type"],
            "balance": a["balance"],
            "currency": a["currency"],
            "status": a["status"],
        }
        for a in rows
    ]
    return {"customer_id": customer_id, "accounts": accounts, "count": len(accounts)}
