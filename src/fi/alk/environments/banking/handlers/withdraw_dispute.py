import json

def handle(args, db):
    customer_id = args.get("customer_id")
    session_id = args.get("session_id")
    dispute_id = args.get("dispute_id")
    reason = args.get("reason")
    confirmed = args.get("confirmed")
    if not customer_id:
        raise ToolError("Missing customer_id.")
    if not session_id:
        raise ToolError("Missing session_id.")
    if not dispute_id:
        raise ToolError("Missing dispute_id.")
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
        raise ToolError("Policy error: explicit customer confirmation is required before withdrawing a dispute.")
    dispute = db.one("SELECT * FROM disputes WHERE dispute_id = ? AND customer_id = ?", [dispute_id, customer_id])
    if not dispute:
        raise ToolError("Dispute record not found for this customer.")
    now_row = db.one("SELECT now FROM clock")
    now_str = now_row["now"] if now_row else "2026-10-26T12:00:00Z"
    if dispute["status"] == "withdrawn":
        return {
            "dispute_id": dispute_id,
            "transaction_id": dispute["transaction_id"],
            "status": "withdrawn",
            "withdrawn_at": dispute["withdrawn_at"],
            "idempotent": True,
            "message": "The dispute was already withdrawn.",
        }
    if dispute["status"] not in ("open", "under_review"):
        raise ToolError(f"Dispute {dispute_id} is {dispute['status']} and cannot be withdrawn; only open or under-review disputes can be.")
    notes = (dispute["notes"] or "") + f" | Withdrawn by customer: {reason}"
    db.execute(
        "UPDATE disputes SET status = 'withdrawn', withdrawn_at = ?, updated_at = ?, notes = ? WHERE dispute_id = ?",
        [now_str, now_str, notes.strip(" |"), dispute_id],
    )
    log_count = len(db.query("SELECT log_id FROM action_log"))
    db.execute(
        "INSERT INTO action_log (log_id, session_id, customer_id, action_type, target_id, payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [f"LOG-{1001 + log_count}", session_id, customer_id, "withdraw_dispute", dispute_id, json.dumps({"reason": reason}), now_str],
    )
    return {
        "dispute_id": dispute_id,
        "transaction_id": dispute["transaction_id"],
        "status": "withdrawn",
        "withdrawn_at": now_str,
        "idempotent": False,
        "message": "The dispute is withdrawn; the charge stands and no provisional credit will be issued.",
    }
