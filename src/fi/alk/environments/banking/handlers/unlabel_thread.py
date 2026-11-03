def handle(args, db):
    thread_id = (args.get("threadId") or "").strip() if isinstance(args.get("threadId"), str) else ""
    if not thread_id:
        raise ToolError("threadId is required", category="invalid_input")
    label_ids = args.get("labelIds")
    if not isinstance(label_ids, list) or not label_ids or not all(isinstance(x, str) and x for x in label_ids):
        raise ToolError("labelIds must be a non-empty list of label IDs", category="invalid_input")

    t = db.one("SELECT * FROM threads WHERE id = ?", (thread_id,))
    if t is None:
        raise ToolError(f"Thread {thread_id!r} not found", category="not_found")
    if t["is_restricted"]:
        raise ToolError(f"Access to thread {thread_id!r} is restricted", category="permission_denied")

    for lid in label_ids:
        if db.one("SELECT id FROM labels WHERE id = ?", (lid,)) is None:
            raise ToolError(f"Label {lid!r} does not exist; labelIds takes label IDs, not names", category="not_found")
    for lid in label_ids:
        db.execute("DELETE FROM thread_labels WHERE thread_id = ? AND label_id = ?", (thread_id, lid))
    return {}
