import json

def handle(args, db):
    customer_id = args.get("customer_id")
    session_id = args.get("session_id")
    transaction_id = args.get("transaction_id")
    dispute_reason = args.get("dispute_reason")
    request_key = args.get("request_key")
    customer_notes = args.get("customer_notes")
    
    if not customer_id:
        raise ToolError("Missing customer_id.")
    if not session_id:
        raise ToolError("Missing session_id.")
    if not transaction_id:
        raise ToolError("Missing transaction_id.")
    if not dispute_reason:
        raise ToolError("Missing dispute_reason.")
        
    sess = db.one("SELECT * FROM sessions WHERE session_id = ?", [session_id])
    if not sess:
        raise ToolError(f"Session {session_id} not found.")
    if sess["customer_id"] != customer_id:
        raise ToolError("Session customer mismatch.")
    if not sess["is_verified"]:
        raise ToolError("Authorization required: customer session is not verified.")
        
    tx = db.one("SELECT * FROM transactions WHERE transaction_id = ?", [transaction_id])
    if not tx:
        raise ToolError(f"Transaction {transaction_id} not found.")
        
    acc = db.one("SELECT * FROM accounts WHERE account_id = ?", [tx["account_id"]])
    if not acc or acc["customer_id"] != customer_id:
        raise ToolError("Transaction does not belong to authenticated customer.")
        
    if tx["status"] == "pending":
        allow_pending_row = db.one("SELECT value FROM environment_policy WHERE name = 'allow_pending_disputes'")
        allow_pending = json.loads(allow_pending_row["value"]) if allow_pending_row else False
        if not allow_pending:
            raise ToolError(f"Transaction {transaction_id} is currently pending. Policy forbids disputing pending charges until posted.")
            
    reasons_row = db.one("SELECT value FROM environment_policy WHERE name = 'allowed_reasons'")
    if reasons_row:
        raw = json.loads(reasons_row["value"])
        allowed = [r.strip() for r in raw.split(",")] if isinstance(raw, str) else raw
        if dispute_reason not in allowed:
            raise ToolError(f"Dispute reason {dispute_reason!r} is not allowed by policy.")
            
    # Check idempotency by request_key
    if request_key:
        existing_by_key = db.one("SELECT * FROM disputes WHERE request_key = ?", [request_key])
        if existing_by_key:
            return {
                "dispute_id": existing_by_key["dispute_id"],
                "transaction_id": existing_by_key["transaction_id"],
                "customer_id": existing_by_key["customer_id"],
                "amount": existing_by_key["amount"],
                "currency": existing_by_key["currency"],
                "status": existing_by_key["status"],
                "dispute_reason": existing_by_key["dispute_reason"],
                "created_at": existing_by_key["created_at"],
                "request_key": existing_by_key["request_key"],
                "customer_notes": existing_by_key["notes"],
                "idempotent": True,
                "next_steps": f"Existing dispute {existing_by_key['dispute_id']} is currently under review."
            }
            
    # Check if transaction already disputed
    existing_by_tx = db.one("SELECT * FROM disputes WHERE transaction_id = ?", [transaction_id])
    if existing_by_tx:
        return {
            "dispute_id": existing_by_tx["dispute_id"],
            "transaction_id": existing_by_tx["transaction_id"],
            "customer_id": existing_by_tx["customer_id"],
            "amount": existing_by_tx["amount"],
            "currency": existing_by_tx["currency"],
            "status": existing_by_tx["status"],
            "dispute_reason": existing_by_tx["dispute_reason"],
            "created_at": existing_by_tx["created_at"],
            "request_key": existing_by_tx["request_key"],
            "customer_notes": existing_by_tx["notes"],
            "idempotent": True,
            "next_steps": f"A dispute ({existing_by_tx['dispute_id']}) is already open for this transaction."
        }
        
    now_row = db.one("SELECT now FROM clock")
    now_str = now_row["now"] if now_row else "2026-10-26T12:00:00Z"
    
    count = len(db.query("SELECT dispute_id FROM disputes"))
    dispute_id = f"D{count + 1:02d}"
    
    db.execute(
        "INSERT INTO disputes (dispute_id, transaction_id, customer_id, amount, currency, dispute_reason, status, created_at, updated_at, request_key, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [dispute_id, transaction_id, customer_id, tx["amount"], tx["currency"], dispute_reason, "open", now_str, now_str, request_key, customer_notes or ""]
    )
    
    log_count = len(db.query("SELECT log_id FROM action_log"))
    log_id = f"LOG-{1001 + log_count}"
    payload = json.dumps({"dispute_id": dispute_id, "transaction_id": transaction_id, "amount": tx["amount"], "reason": dispute_reason, "request_key": request_key, "notes": customer_notes})
    db.execute(
        "INSERT INTO action_log (log_id, session_id, customer_id, action_type, target_id, payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [log_id, session_id, customer_id, "create_dispute", dispute_id, payload, now_str]
    )
    
    return {
        "dispute_id": dispute_id,
        "transaction_id": transaction_id,
        "customer_id": customer_id,
        "amount": tx["amount"],
        "currency": tx["currency"],
        "dispute_reason": dispute_reason,
        "status": "open",
        "created_at": now_str,
        "request_key": request_key,
        "customer_notes": customer_notes or "",
        "idempotent": False,
        "next_steps": f"Dispute {dispute_id} has been opened. Our fraud operations team will review within 10 business days."
    }
