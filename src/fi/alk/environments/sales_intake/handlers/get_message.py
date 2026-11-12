def handle(args, db):
    message_id = (args.get("messageId") or "").strip() if isinstance(args.get("messageId"), str) else ""
    if not message_id:
        raise ToolError("messageId is required", category="invalid_input")
    message_format = args.get("messageFormat") or "FULL_CONTENT"
    if message_format not in ("MESSAGE_FORMAT_UNSPECIFIED", "MINIMAL", "FULL_CONTENT", "METADATA_ONLY"):
        raise ToolError(f"Unknown messageFormat {message_format!r}", category="invalid_input")

    m = db.one("SELECT * FROM gmail_messages WHERE id = ?", (message_id,))
    if m is None:
        raise ToolError(f"Message {message_id!r} not found", category="not_found")
    t = db.one("SELECT is_restricted FROM gmail_threads WHERE id = ?", (m["thread_id"],))
    if t and t["is_restricted"]:
        raise ToolError(f"Access to message {message_id!r} is restricted by mailbox ACL", category="permission_denied")

    if not _sees(db, m["thread_id"]):
        raise ToolError(f"Permission denied: message {message_id!r} is not the correspondence of the person in this conversation.", category="permission_denied")
    labels = [row["label_id"] for row in db.query("SELECT label_id FROM gmail_thread_labels WHERE thread_id = ?", (m["thread_id"],))]
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


def _caller(db):
    """Who is in this conversation, as the session set it: ("contact", row) for a prospect speaking
    for themselves, ("staff", row) for a BrightDesk employee (their Slack user), or (None, None).
    An ID or an email the desk is told never changes it."""
    row = db.one("SELECT person_id FROM session_caller WHERE id = 'default'")
    person = row["person_id"] if row else None
    if person:
        contact = db.one("SELECT * FROM contacts WHERE contact_id = ?", [person])
        if contact:
            return "contact", contact
        staff = db.one("SELECT * FROM slack_users WHERE user_id = ?", [person])
        if staff:
            return "staff", staff
    return None, None


def _named(kind, who):
    if kind == "contact":
        return f"{who['name']} ({who['contact_id']})"
    if kind == "staff":
        return f"{who['name']} ({who['user_id']})"
    return "nobody signed in"


def _sees(db, thread_id):
    """Whether the person in this conversation may read this thread: staff read the sales@ mailbox;
    a prospect only a thread they wrote or were written to."""
    kind, who = _caller(db)
    if kind == "staff":
        return True
    if kind != "contact":
        return False
    mine = who["email_normalized"]
    return any(
        m["sender"].strip().lower() == mine or mine in [x.strip().lower() for x in (m["recipient"] + "," + (m["cc"] or "")).split(",")]
        for m in db.query("SELECT sender, recipient, cc FROM gmail_messages WHERE thread_id = ?", (thread_id,))
    )