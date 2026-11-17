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
        cards = db.query("SELECT card_id, account_id, customer_id, card_number_masked, last_four, card_type, status, expiration_date, locked_at FROM cards WHERE customer_id = ? AND status = ?", [customer_id, status])
    else:
        cards = db.query("SELECT card_id, account_id, customer_id, card_number_masked, last_four, card_type, status, expiration_date, locked_at FROM cards WHERE customer_id = ?", [customer_id])
        
    return {
        "customer_id": customer_id,
        "cards": cards,
        "count": len(cards)
    }
