def handle(args, db):
    thread_id = (args.get("threadId") or "").strip() if isinstance(args.get("threadId"), str) else ""
    if not thread_id:
        raise ToolError("threadId is required", category="invalid_input")
    message_format = args.get("messageFormat") or "FULL_CONTENT"
    if message_format not in ("MESSAGE_FORMAT_UNSPECIFIED", "MINIMAL", "FULL_CONTENT", "METADATA_ONLY"):
        raise ToolError(f"Unknown messageFormat {message_format!r}", category="invalid_input")
    full = message_format in ("FULL_CONTENT", "MESSAGE_FORMAT_UNSPECIFIED")

    t = db.one("SELECT * FROM gmail_threads WHERE id = ?", (thread_id,))
    if t is None:
        raise ToolError(f"Thread {thread_id!r} not found", category="not_found")
    if t["is_restricted"]:
        raise ToolError(f"Access to thread {thread_id!r} is restricted by mailbox ACL", category="permission_denied")

    labels = [row["label_id"] for row in db.query("SELECT label_id FROM gmail_thread_labels WHERE thread_id = ?", (thread_id,))]
    messages = []
    for m in db.query("SELECT * FROM gmail_messages WHERE thread_id = ? ORDER BY date ASC", (thread_id,)):
        message = {
            "id": m["id"],
            "snippet": m["snippet"],
            "subject": m["subject"],
            "sender": m["sender"],
            "toRecipients": [x.strip() for x in m["recipient"].split(",") if x.strip()],
            "ccRecipients": [x.strip() for x in (m["cc"] or "").split(",") if x.strip()],
            "date": m["date"][:10],
            "attachmentIds": [],
            "attachments": [],
            "labelIds": labels,
        }
        if full:
            message["plaintextBody"] = m["body"]
            message["htmlBody"] = ""
        messages.append(message)
    return {"id": t["id"], "messages": messages}
