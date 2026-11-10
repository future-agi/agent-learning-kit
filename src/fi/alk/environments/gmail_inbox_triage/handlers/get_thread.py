def handle(args, db):
    thread_id = (args.get("thread_id") or "").strip()
    if not thread_id:
        raise ToolError("thread_id is required", category="invalid_input")
        
    t = db.one("SELECT * FROM threads WHERE id = ?", (thread_id,))
    if t is None:
        raise ToolError(f"Thread {thread_id!r} not found", category="not_found")
        
    if t["is_restricted"]:
        raise ToolError(f"Access to thread {thread_id!r} is restricted by mailbox ACL", category="permission_denied")
        
    labels = [row["label_id"] for row in db.query("SELECT label_id FROM thread_labels WHERE thread_id = ?", (thread_id,))]
    msgs = db.query("SELECT * FROM messages WHERE thread_id = ? ORDER BY date ASC", (thread_id,))
    
    return {
        "id": t["id"],
        "history_id": t["history_id"],
        "snippet": t["snippet"],
        "label_ids": labels,
        "messages": msgs
    }
