def handle(args, db):
    database_id = args.get("database_id")
    title = args.get("title")
    owner_id = args.get("owner_id")
    due_date = args.get("due_date")
    source_page_id = args.get("source_page_id")
    if not database_id:
        raise ToolError("Missing required parameter 'database_id'.")
    if not title:
        raise ToolError("Missing required parameter 'title'.")
    if not owner_id:
        raise ToolError("Missing required parameter 'owner_id'.")
    if not due_date:
        raise ToolError("Missing required parameter 'due_date'.")
    if not source_page_id:
        raise ToolError("Missing required parameter 'source_page_id'.")

    db_row = db.one("SELECT * FROM databases WHERE database_id = ?", (database_id,))
    if db_row is None:
        raise ToolError(f"Database '{database_id}' not found.")
    if not db_row["allowed_access"]:
        raise ToolError(f"Access denied to database '{database_id}'.")

    page = db.one("SELECT * FROM pages WHERE page_id = ?", (source_page_id,))
    if page is None:
        raise ToolError(f"Source page '{source_page_id}' not found.")
    if not page["allowed_access"]:
        raise ToolError(f"Access denied to source page '{source_page_id}'.")
    if page["archived"]:
        raise ToolError(f"Cannot create action for archived page '{source_page_id}'.")

    user = db.one("SELECT * FROM workspace_users WHERE user_id = ?", (owner_id,))
    if user is None:
        raise ToolError(f"Owner user '{owner_id}' not found.")
    if user["status"] == "read_only":
        raise ToolError(f"User '{user['name']}' has read-only permissions and cannot be assigned tasks.")

    status = args.get("status") or "Not Started"
    external_key = args.get("external_key") or f"{source_page_id}-item-{int(db.one('SELECT count(*) as cnt FROM action_records')['cnt']) + 1}"
    is_async = args.get("is_async", False)

    existing_count = int(db.one("SELECT count(*) as cnt FROM action_records")["cnt"])
    action_id = f"act_{100 + existing_count + 1}"

    db.execute(
        "INSERT INTO action_records (action_id, database_id, title, owner_id, due_date, status, source_page_id, external_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (action_id, database_id, title, owner_id, due_date, status, source_page_id, external_key)
    )

    now = db.one("SELECT now FROM clock")["now"]
    evt_count = int(db.one("SELECT count(*) as cnt FROM audit_log")["cnt"])
    evt_id = f"evt_{100 + evt_count + 1}"
    db.execute(
        "INSERT INTO audit_log (event_id, timestamp, actor_id, action, target_id, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
        (evt_id, now, owner_id, "create_action", action_id, json.dumps({"title": title, "source_page": source_page_id}))
    )

    async_task_id = None
    res_status = "created"
    if is_async:
        task_count = int(db.one("SELECT count(*) as cnt FROM async_tasks")["cnt"])
        async_task_id = f"task_{200 + task_count + 1}"
        db.execute(
            "INSERT INTO async_tasks (task_id, status, operation, result_json, error_message) VALUES (?, ?, ?, ?, ?)",
            (async_task_id, "completed", "notion_create_action_record", json.dumps({"action_id": action_id}), "")
        )
        res_status = "pending"

    return {"action_id": action_id, "async_task_id": async_task_id, "status": res_status}