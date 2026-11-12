def _addresses(*fields):
    """The complete mailbox addresses in address fields ("a@x, Name <b@y>"), lower-cased. Only a
    whole address is one: a display name, or a longer address that contains it, is not."""
    from email.utils import getaddresses

    return {address.strip().lower() for _, address in getaddresses([f for f in fields if f]) if "@" in address}


def _caller(db):
    """Who is in this conversation, as the session knows it (session_caller), never as the
    conversation tells it: a customer, a front-desk member, or nobody identified."""
    row = db.one("SELECT customer_id, desk_id FROM session_caller WHERE id = 'default'")
    return (row["customer_id"], row["desk_id"]) if row else (None, None)


def _desk(db):
    """Whether the caller is an active front-desk member, who may act for any customer."""
    _, desk_id = _caller(db)
    return bool(desk_id and db.one("SELECT 1 AS x FROM desk_staff WHERE desk_id = ? AND role = 'front_desk' AND active = 1", [desk_id]))


def _mail_scope(db):
    """The threads the caller may read: every thread for the front desk, who works the bookings
    mailbox; for a customer only those they wrote or were written to; for nobody identified none.
    None means every thread."""
    if _desk(db):
        return None
    caller, _ = _caller(db)
    row = db.one("SELECT email FROM customers WHERE customer_id = ?", [caller]) if caller else None
    if not row:
        return set()
    me = row["email"].lower()
    return {m["thread_id"] for m in db.query("SELECT thread_id, sender, recipient, cc FROM gmail_messages")
            if me in _addresses(m["sender"], m["recipient"], m["cc"])}


def _thread_allowed(db, thread_id):
    scope = _mail_scope(db)
    if scope is not None and thread_id not in scope:
        raise ToolError(f"Access to thread {thread_id!r} is limited to the front desk and the people on it", category="permission_denied")

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
    _thread_allowed(db, thread_id)

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
