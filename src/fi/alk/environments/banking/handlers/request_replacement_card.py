import json

def handle(args, db):
    customer_id = args.get("customer_id")
    session_id = args.get("session_id")
    card_id = args.get("card_id")
    reason = args.get("reason")
    delivery_speed = args.get("delivery_speed") or "standard"
    confirmed = args.get("confirmed")
    request_key = args.get("request_key")
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
    if reason not in ("lost", "stolen", "damaged", "expired", "not_received"):
        raise ToolError(f"Unknown replacement reason {reason!r}; use lost, stolen, damaged, expired or not_received.")
    if delivery_speed not in ("standard", "expedited"):
        raise ToolError(f"Unknown delivery_speed {delivery_speed!r}; use standard or expedited.")
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
        raise ToolError("Policy error: explicit customer confirmation is required before requesting a replacement card.")
    card = db.one("SELECT * FROM cards WHERE card_id = ? AND customer_id = ?", [card_id, customer_id])
    if not card:
        raise ToolError(f"Card {card_id} not found for customer {customer_id}.")
    if reason in ("lost", "stolen") and card["status"] != "locked":
        raise ToolError("Policy: a lost or stolen card must be locked before a replacement is issued. Lock the card first.")
    now_row = db.one("SELECT now FROM clock")
    now_str = now_row["now"] if now_row else "2026-10-26T12:00:00Z"
    fee_row = db.one("SELECT value FROM environment_policy WHERE name = 'expedited_replacement_fee'")
    expedited_fee = int(json.loads(fee_row["value"])) if fee_row else 2500
    fee = expedited_fee if delivery_speed == "expedited" else 0
    eta = "2" if delivery_speed == "expedited" else "5-7"

    def present(row, idempotent):
        return {
            "replacement_id": row["replacement_id"],
            "card_id": row["card_id"],
            "last_four": card["last_four"],
            "reason": row["reason"],
            "delivery_speed": row["delivery_speed"],
            "fee": row["fee"],
            "currency": row["currency"],
            "eta_business_days": "2" if row["delivery_speed"] == "expedited" else "5-7",
            "status": row["status"],
            "request_key": row["request_key"],
            "created_at": row["created_at"],
            "idempotent": idempotent,
        }

    if request_key:
        existing = db.one("SELECT * FROM replacement_requests WHERE request_key = ?", [request_key])
        if existing:
            return present(existing, True)
    existing = db.one("SELECT * FROM replacement_requests WHERE card_id = ? AND status = 'requested'", [card_id])
    if existing:
        return present(existing, True)
    count = len(db.query("SELECT replacement_id FROM replacement_requests"))
    replacement_id = f"RPL-{1001 + count}"
    db.execute(
        "INSERT INTO replacement_requests (replacement_id, card_id, customer_id, reason, delivery_speed, fee, currency, status, request_key, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [replacement_id, card_id, customer_id, reason, delivery_speed, fee, "USD", "requested", request_key, now_str],
    )
    log_count = len(db.query("SELECT log_id FROM action_log"))
    payload = json.dumps({"replacement_id": replacement_id, "card_id": card_id, "reason": reason, "delivery_speed": delivery_speed, "fee": fee})
    db.execute(
        "INSERT INTO action_log (log_id, session_id, customer_id, action_type, target_id, payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [f"LOG-{1001 + log_count}", session_id, customer_id, "request_replacement_card", replacement_id, payload, now_str],
    )
    row = db.one("SELECT * FROM replacement_requests WHERE replacement_id = ?", [replacement_id])
    result = present(row, False)
    result["eta_business_days"] = eta
    return result
