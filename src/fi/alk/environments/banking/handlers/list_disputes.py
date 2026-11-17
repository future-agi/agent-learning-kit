def handle(args, db):
    customer_id = args.get("customer_id")
    session_id = args.get("session_id")
    status = args.get("status")
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
    if status:
        rows = db.query("SELECT * FROM disputes WHERE customer_id = ? AND status = ? ORDER BY created_at", [customer_id, status])
    else:
        rows = db.query("SELECT * FROM disputes WHERE customer_id = ? ORDER BY created_at", [customer_id])
    disputes = [
        {
            "dispute_id": d["dispute_id"],
            "transaction_id": d["transaction_id"],
            "amount": d["amount"],
            "currency": d["currency"],
            "dispute_reason": d["dispute_reason"],
            "status": d["status"],
            "created_at": d["created_at"],
            "updated_at": d["updated_at"],
            "resolution": d["resolution"] or "",
        }
        for d in rows
    ]
    return {"customer_id": customer_id, "disputes": disputes, "count": len(disputes)}
