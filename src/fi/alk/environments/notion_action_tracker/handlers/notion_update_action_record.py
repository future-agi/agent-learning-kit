def handle(args, db):
    action_id = args.get("action_id")
    if not action_id:
        raise ToolError("Missing required parameter 'action_id'.")

    rec = db.one("SELECT * FROM action_records WHERE action_id = ?", (action_id,))
    if rec is None:
        raise ToolError(f"Action record '{action_id}' not found.")

    title = args.get("title", rec["title"])
    owner_id = args.get("owner_id", rec["owner_id"])
    due_date = args.get("due_date", rec["due_date"])
    status = args.get("status", rec["status"])

    if owner_id != rec["owner_id"]:
        user = db.one("SELECT * FROM workspace_users WHERE user_id = ?", (owner_id,))
        if user is None:
            raise ToolError(f"Owner user '{owner_id}' not found.")
        if user["status"] == "read_only":
            raise ToolError(f"User '{user['name']}' has read-only permissions and cannot be assigned tasks.")

    db.execute(
        "UPDATE action_records SET title = ?, owner_id = ?, due_date = ?, status = ? WHERE action_id = ?",
        (title, owner_id, due_date, status, action_id)
    )

    now = db.one("SELECT now FROM clock")["now"]
    evt_count = int(db.one("SELECT count(*) as cnt FROM audit_log")["cnt"])
    evt_id = f"evt_{100 + evt_count + 1}"
    db.execute(
        "INSERT INTO audit_log (event_id, timestamp, actor_id, action, target_id, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
        (evt_id, now, owner_id, "update_action", action_id, json.dumps({"status": status, "title": title}))
    )

    return {"action_id": action_id, "status": status, "updated": True}