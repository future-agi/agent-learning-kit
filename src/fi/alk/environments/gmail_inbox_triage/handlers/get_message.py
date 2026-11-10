def handle(args, db):
    message_id = (args.get("message_id") or "").strip()
    if not message_id:
        raise ToolError("message_id is required", category="invalid_input")
        
    msg = db.one("SELECT * FROM messages WHERE id = ?", (message_id,))
    if msg is None:
        raise ToolError(f"Message {message_id!r} not found", category="not_found")
        
    t = db.one("SELECT is_restricted FROM threads WHERE id = ?", (msg["thread_id"],))
    if t and t["is_restricted"]:
        raise ToolError(f"Access to message {message_id!r} is restricted by mailbox ACL", category="permission_denied")
        
    return {
        "id": msg["id"],
        "thread_id": msg["thread_id"],
        "sender": msg["sender"],
        "recipient": msg["recipient"],
        "cc": msg["cc"],
        "subject": msg["subject"],
        "body": msg["body"],
        "date": msg["date"],
        "snippet": msg["snippet"]
    }
