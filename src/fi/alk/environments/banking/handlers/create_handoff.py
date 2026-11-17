import json

def handle(args, db):
    customer_id = args.get("customer_id")
    session_id = args.get("session_id")
    reason = args.get("reason")
    department = args.get("department", "fraud_specialist")
    context = args.get("context", "")
    card_id = args.get("card_id", "")
    transaction_id = args.get("transaction_id", "")
    
    if not customer_id:
        raise ToolError("Missing customer_id.")
    if not session_id:
        raise ToolError("Missing session_id.")
    if not reason:
        raise ToolError("Missing reason.")
        
    sess = db.one("SELECT * FROM sessions WHERE session_id = ?", [session_id])
    if not sess:
        raise ToolError(f"Session {session_id} not found.")
    if sess["customer_id"] != customer_id:
        raise ToolError("Session customer mismatch.")
        
    now_row = db.one("SELECT now FROM clock")
    now_str = now_row["now"] if now_row else "2026-10-26T12:00:00Z"
    
    count = len(db.query("SELECT handoff_id FROM handoffs"))
    handoff_id = f"HND-{1001 + count}"
    
    db.execute(
        "INSERT INTO handoffs (handoff_id, session_id, customer_id, department, reason, context, card_id, transaction_id, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [handoff_id, session_id, customer_id, department, reason, context, card_id, transaction_id, "routed", now_str]
    )
    
    log_count = len(db.query("SELECT log_id FROM action_log"))
    log_id = f"LOG-{1001 + log_count}"
    payload = json.dumps({"handoff_id": handoff_id, "department": department, "reason": reason, "card_id": card_id, "transaction_id": transaction_id, "context": context})
    db.execute(
        "INSERT INTO action_log (log_id, session_id, customer_id, action_type, target_id, payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [log_id, session_id, customer_id, "create_handoff", handoff_id, payload, now_str]
    )
    
    return {
        "handoff_id": handoff_id,
        "customer_id": customer_id,
        "session_id": session_id,
        "department": department,
        "reason": reason,
        "context": context,
        "card_id": card_id,
        "transaction_id": transaction_id,
        "status": "routed",
        "created_at": now_str
    }
