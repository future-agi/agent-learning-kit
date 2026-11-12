def _addresses(*fields):
    """The complete mailbox addresses in address fields ("a@x, Name <b@y>"), lower-cased. Only a
    whole address is one: a display name, or a longer address that contains it, is not."""
    from email.utils import getaddresses

    return {address.strip().lower() for _, address in getaddresses([f for f in fields if f]) if "@" in address}


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

    if not _sees(db, thread_id):
        raise ToolError(f"Permission denied: thread {thread_id!r} is not the correspondence of the person in this conversation.", category="permission_denied")
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
        mine in _addresses(m["sender"], m["recipient"], m["cc"])
        for m in db.query("SELECT sender, recipient, cc FROM gmail_messages WHERE thread_id = ?", (thread_id,))
    )