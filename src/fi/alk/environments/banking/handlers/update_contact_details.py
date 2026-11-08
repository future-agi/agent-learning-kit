import json

def handle(args, db):
    customer_id = args.get("customer_id")
    session_id = args.get("session_id")
    field = args.get("field")
    new_value = args.get("new_value")
    request_key = args.get("request_key")
    if not customer_id:
        raise ToolError("Missing customer_id.")
    if not session_id:
        raise ToolError("Missing session_id.")
    if field not in ("phone", "email", "address"):
        raise ToolError(f"Unknown field {field!r}; use phone, email or address.")
    if not isinstance(new_value, str) or not new_value.strip():
        raise ToolError("Missing new_value.")
    new_value = new_value.strip()
    if field == "email" and ("@" not in new_value or " " in new_value):
        raise ToolError("new_value is not an email address.")
    if field == "phone" and not new_value.lstrip("+").replace(" ", "").isdigit():
        raise ToolError("new_value is not a phone number.")
    sess = db.one("SELECT * FROM sessions WHERE session_id = ?", [session_id])
    if not sess:
        raise ToolError(f"Session {session_id} not found.")
    if sess["customer_id"] != customer_id:
        raise ToolError("Session customer mismatch.")
    if not sess["is_verified"]:
        raise ToolError("Authorization required: customer session is not verified.")
    if not sess["step_up_at"]:
        raise ToolError("Step-up verification required: complete a one-time passcode (otp) challenge with verify_identity before changing contact details.")
    cust = db.one("SELECT * FROM customers WHERE customer_id = ?", [customer_id])
    if not cust:
        raise ToolError(f"Customer {customer_id} not found.")
    now_row = db.one("SELECT now FROM clock")
    now_str = now_row["now"] if now_row else "2026-10-26T12:00:00Z"
    if request_key:
        existing = db.one("SELECT * FROM contact_change_requests WHERE request_key = ?", [request_key])
        if existing:
            return {
                "change_id": existing["change_id"],
                "customer_id": customer_id,
                "field": existing["field"],
                "old_value": existing["old_value"],
                "new_value": existing["new_value"],
                "status": existing["status"],
                "idempotent": True,
                "confirmation_sent_to": [existing["old_value"], existing["new_value"]] if existing["field"] == "email" else [cust["email"]],
            }
    old_value = cust[field] or ""
    count = len(db.query("SELECT change_id FROM contact_change_requests"))
    change_id = f"CHG-{1001 + count}"
    db.execute(
        "INSERT INTO contact_change_requests (change_id, customer_id, field, old_value, new_value, step_up_method, status, request_key, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [change_id, customer_id, field, old_value, new_value, "otp", "applied", request_key, now_str],
    )
    db.execute(f"UPDATE customers SET {field} = ? WHERE customer_id = ?", [new_value, customer_id])
    log_count = len(db.query("SELECT log_id FROM action_log"))
    db.execute(
        "INSERT INTO action_log (log_id, session_id, customer_id, action_type, target_id, payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [f"LOG-{1001 + log_count}", session_id, customer_id, "update_contact_details", change_id, json.dumps({"field": field, "old_value": old_value, "new_value": new_value}), now_str],
    )
    return {
        "change_id": change_id,
        "customer_id": customer_id,
        "field": field,
        "old_value": old_value,
        "new_value": new_value,
        "status": "applied",
        "idempotent": False,
        "confirmation_sent_to": [old_value, new_value] if field == "email" else [cust["email"]],
    }
