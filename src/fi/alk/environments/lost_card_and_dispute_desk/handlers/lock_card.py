import json

def handle(args, db):
    customer_id = args.get("customer_id")
    session_id = args.get("session_id")
    card_id = args.get("card_id")
    reason = args.get("reason")
    confirmed = args.get("confirmed")
    
    if not customer_id:
        raise ToolError("Missing customer_id.")
    if not session_id:
        raise ToolError("Missing session_id.")
    if not card_id:
        raise ToolError("Missing card_id.")
    if not reason:
        raise ToolError("Missing reason.")
    if confirmed is None:
        raise ToolError("Missing confirmed parameter.")
        
    sess = db.one("SELECT * FROM sessions WHERE session_id = ?", [session_id])
    if not sess:
        raise ToolError(f"Session {session_id} not found.")
    if sess["customer_id"] != customer_id:
        raise ToolError("Session customer mismatch.")
    if not sess["is_verified"]:
        raise ToolError("Authorization required: customer session is not verified.")
        
    req_conf_row = db.one("SELECT value FROM environment_policy WHERE name = 'require_confirmation'")
    require_conf = json.loads(req_conf_row["value"]) if req_conf_row else True
    if require_conf and not confirmed:
        raise ToolError("Policy error: Explicit customer confirmation is required before locking a card.")
        
    card = db.one("SELECT * FROM cards WHERE card_id = ? AND customer_id = ?", [card_id, customer_id])
    if not card:
        raise ToolError(f"Card {card_id} not found for customer {customer_id}.")
        
    now_row = db.one("SELECT now FROM clock")
    now_str = now_row["now"] if now_row else "2026-10-26T12:00:00Z"
    
    if card["status"] == "locked":
        return {
            "card_id": card_id,
            "status": "locked",
            "locked_at": card["locked_at"],
            "last_four": card["last_four"],
            "idempotent": True,
            "message": "Card is already locked."
        }
        
    db.execute("UPDATE cards SET status = 'locked', locked_at = ? WHERE card_id = ?", [now_str, card_id])
    
    log_count = len(db.query("SELECT log_id FROM action_log"))
    log_id = f"LOG-{1001 + log_count}"
    payload = json.dumps({"card_id": card_id, "reason": reason, "confirmed": confirmed})
    db.execute(
        "INSERT INTO action_log (log_id, session_id, customer_id, action_type, target_id, payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [log_id, session_id, customer_id, "lock_card", card_id, payload, now_str]
    )
    
    return {
        "card_id": card_id,
        "status": "locked",
        "locked_at": now_str,
        "last_four": card["last_four"],
        "idempotent": False,
        "reason": reason
    }
