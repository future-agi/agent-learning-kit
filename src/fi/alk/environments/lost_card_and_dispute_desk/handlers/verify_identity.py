import json

def handle(args, db):
    customer_id = args.get("customer_id")
    session_id = args.get("session_id")
    challenge_type = args.get("challenge_type")
    challenge_response = args.get("challenge_response")
    
    if not customer_id:
        raise ToolError("Missing customer_id.")
    if not session_id:
        raise ToolError("Missing session_id.")
    if not challenge_type:
        raise ToolError("Missing challenge_type.")
    if not challenge_response:
        raise ToolError("Missing challenge_response.")
    
    now_row = db.one("SELECT now FROM clock")
    now_str = now_row["now"] if now_row else "2026-10-26T12:00:00Z"
    
    sess = db.one("SELECT * FROM sessions WHERE session_id = ?", [session_id])
    if not sess:
        raise ToolError(f"Session {session_id} not found.")
    if sess["customer_id"] != customer_id:
        raise ToolError("Session customer mismatch.")
        
    cust = db.one("SELECT * FROM customers WHERE customer_id = ?", [customer_id])
    if not cust:
        raise ToolError(f"Customer {customer_id} not found.")
        
    if challenge_type == "passcode":
        expected = cust["passcode"]
    elif challenge_type == "kba":
        expected = cust["kba_answer"]
    elif challenge_type == "otp":
        expected = cust["otp_code"]
    else:
        raise ToolError(f"Unsupported challenge type: {challenge_type}")
        
    if str(challenge_response).strip().lower() != str(expected).strip().lower():
        new_attempts = sess["failed_attempts"] + 1
        db.execute("UPDATE sessions SET failed_attempts = ? WHERE session_id = ?", [new_attempts, session_id])
        log_id = f"LOG-{session_id}-{new_attempts}"
        db.execute(
            "INSERT INTO action_log (log_id, session_id, customer_id, action_type, target_id, payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [log_id, session_id, customer_id, "verify_identity_failed", challenge_type, json.dumps({"challenge_type": challenge_type, "status": "failed"}), now_str]
        )
        raise ToolError("Verification failed: challenge response does not match records.")
        
    db.execute("UPDATE sessions SET is_verified = ?, verified_at = ?, failed_attempts = 0 WHERE session_id = ?", [True, now_str, session_id])
    log_id = f"LOG-{session_id}-V"
    db.execute(
        "INSERT INTO action_log (log_id, session_id, customer_id, action_type, target_id, payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [log_id, session_id, customer_id, "verify_identity_success", challenge_type, json.dumps({"challenge_type": challenge_type, "status": "verified"}), now_str]
    )
    return {
        "status": "verified",
        "customer_id": customer_id,
        "session_id": session_id,
        "verified_at": now_str
    }
