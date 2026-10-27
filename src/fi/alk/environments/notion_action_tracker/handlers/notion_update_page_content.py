def handle(args, db):
    page_id = args.get("page_id")
    append_content = args.get("append_content")
    if not page_id:
        raise ToolError("Missing required parameter 'page_id'.")
    if not append_content:
        raise ToolError("Missing required parameter 'append_content'.")

    page = db.one("SELECT * FROM pages WHERE page_id = ?", (page_id,))
    if page is None:
        raise ToolError(f"Page '{page_id}' not found.")
    if not page["allowed_access"]:
        raise ToolError(f"Access denied to page '{page_id}'.")
    if page["archived"]:
        raise ToolError(f"Cannot modify archived page '{page_id}'.")

    new_content = page["content_markdown"] + "\n\n" + append_content
    db.execute("UPDATE pages SET content_markdown = ? WHERE page_id = ?", (new_content, page_id))

    now = db.one("SELECT now FROM clock")["now"]
    evt_count = int(db.one("SELECT count(*) as cnt FROM audit_log")["cnt"])
    evt_id = f"evt_{100 + evt_count + 1}"
    db.execute(
        "INSERT INTO audit_log (event_id, timestamp, actor_id, action, target_id, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
        (evt_id, now, "system", "update_page_content", page_id, json.dumps({"appended_len": len(append_content)}))
    )

    return {"page_id": page_id, "updated": True}