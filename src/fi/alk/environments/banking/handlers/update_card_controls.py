import json

def handle(args, db):
    customer_id = args.get("customer_id")
    session_id = args.get("session_id")
    card_id = args.get("card_id")
    confirmed = args.get("confirmed")
    if not customer_id:
        raise ToolError("Missing customer_id.")
    if not session_id:
        raise ToolError("Missing session_id.")
    if not card_id:
        raise ToolError("Missing card_id.")
    if confirmed is None:
        raise ToolError("Missing confirmed parameter.")
    changes = {}
    for name in ("international_enabled", "online_enabled", "contactless_enabled"):
        if args.get(name) is not None:
            if not isinstance(args[name], bool):
                raise ToolError(f"{name} must be true or false.")
            changes[name] = args[name]
    if args.get("daily_limit") is not None:
        limit = args["daily_limit"]
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ToolError("daily_limit must be a positive integer amount in minor units (cents).")
        max_row = db.one("SELECT value FROM environment_policy WHERE name = 'max_daily_limit'")
        max_limit = int(json.loads(max_row["value"])) if max_row else 500000
        if limit > max_limit:
            raise ToolError(f"Policy: the daily limit cannot exceed {max_limit} minor units (${max_limit / 100:,.2f}).")
        changes["daily_limit"] = limit
    if not changes:
        raise ToolError("No control to change: give at least one of international_enabled, online_enabled, contactless_enabled or daily_limit.")
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
        raise ToolError("Policy error: explicit customer confirmation is required before changing card controls.")
    card = db.one("SELECT * FROM cards WHERE card_id = ? AND customer_id = ?", [card_id, customer_id])
    if not card:
        raise ToolError(f"Card {card_id} not found for customer {customer_id}.")
    if card["status"] != "active":
        raise ToolError(f"Policy: controls cannot be changed on a card that is {card['status']}.")
    now_row = db.one("SELECT now FROM clock")
    now_str = now_row["now"] if now_row else "2026-10-26T12:00:00Z"
    current = db.one("SELECT * FROM card_controls WHERE card_id = ?", [card_id])
    if current is None:
        db.execute(
            "INSERT INTO card_controls (card_id, international_enabled, online_enabled, contactless_enabled, daily_limit, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            [card_id, False, True, True, 250000, now_str],
        )
        current = db.one("SELECT * FROM card_controls WHERE card_id = ?", [card_id])
    merged = {
        "international_enabled": bool(current["international_enabled"]),
        "online_enabled": bool(current["online_enabled"]),
        "contactless_enabled": bool(current["contactless_enabled"]),
        "daily_limit": int(current["daily_limit"]),
    }
    merged.update(changes)
    db.execute(
        "UPDATE card_controls SET international_enabled = ?, online_enabled = ?, contactless_enabled = ?, daily_limit = ?, updated_at = ? WHERE card_id = ?",
        [merged["international_enabled"], merged["online_enabled"], merged["contactless_enabled"], merged["daily_limit"], now_str, card_id],
    )
    log_count = len(db.query("SELECT log_id FROM action_log"))
    db.execute(
        "INSERT INTO action_log (log_id, session_id, customer_id, action_type, target_id, payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [f"LOG-{1001 + log_count}", session_id, customer_id, "update_card_controls", card_id, json.dumps(changes), now_str],
    )
    return {
        "card_id": card_id,
        "last_four": card["last_four"],
        "international_enabled": merged["international_enabled"],
        "online_enabled": merged["online_enabled"],
        "contactless_enabled": merged["contactless_enabled"],
        "daily_limit": merged["daily_limit"],
        "currency": "USD",
        "changed": sorted(changes),
        "updated_at": now_str,
    }
