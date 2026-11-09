def handle(args, db):
    customer_id = args.get("customer_id")
    session_id = args.get("session_id")
    dispute_id = args.get("dispute_id")
    transaction_id = args.get("transaction_id")
    
    if not customer_id:
        raise ToolError("Missing customer_id.")
    if not session_id:
        raise ToolError("Missing session_id.")
    if not dispute_id and not transaction_id:
        raise ToolError("Must provide either dispute_id or transaction_id.")
        
    sess = db.one("SELECT * FROM sessions WHERE session_id = ?", [session_id])
    if not sess:
        raise ToolError(f"Session {session_id} not found.")
    if sess["customer_id"] != customer_id:
        raise ToolError("Session customer mismatch.")
    if not sess["is_verified"]:
        raise ToolError("Authorization required: customer session is not verified.")
        
    if dispute_id:
        dispute = db.one("SELECT * FROM disputes WHERE dispute_id = ? AND customer_id = ?", [dispute_id, customer_id])
    else:
        dispute = db.one("SELECT * FROM disputes WHERE transaction_id = ? AND customer_id = ?", [transaction_id, customer_id])
        
    if not dispute:
        raise ToolError("Dispute record not found for this customer.")
        
    return {
        "dispute_id": dispute["dispute_id"],
        "transaction_id": dispute["transaction_id"],
        "customer_id": dispute["customer_id"],
        "amount": dispute["amount"],
        "currency": dispute["currency"],
        "dispute_reason": dispute["dispute_reason"],
        "status": dispute["status"],
        "created_at": dispute["created_at"],
        "updated_at": dispute["updated_at"],
        "notes": dispute["notes"] or ""
    }
