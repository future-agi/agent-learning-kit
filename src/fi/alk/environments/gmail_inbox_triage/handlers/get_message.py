def handle(args, db):
    message_id = (args.get("messageId") or "").strip() if isinstance(args.get("messageId"), str) else ""
    if not message_id:
        raise ToolError("messageId is required", category="invalid_input")
    message_format = args.get("messageFormat") or "FULL_CONTENT"
    if message_format not in ("MESSAGE_FORMAT_UNSPECIFIED", "MINIMAL", "FULL_CONTENT", "METADATA_ONLY"):
        raise ToolError(f"Unknown messageFormat {message_format!r}", category="invalid_input")

    m = db.one("SELECT * FROM messages WHERE id = ?", (message_id,))
    if m is None:
        raise ToolError(f"Message {message_id!r} not found", category="not_found")
    t = db.one("SELECT is_restricted FROM threads WHERE id = ?", (m["thread_id"],))
    if t and t["is_restricted"]:
        raise ToolError(f"Access to message {message_id!r} is restricted by mailbox ACL", category="permission_denied")

    labels = [row["label_id"] for row in db.query("SELECT label_id FROM thread_labels WHERE thread_id = ?", (m["thread_id"],))]
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
    if message_format in ("FULL_CONTENT", "MESSAGE_FORMAT_UNSPECIFIED"):
        message["plaintextBody"] = m["body"]
        message["htmlBody"] = ""
    return message
