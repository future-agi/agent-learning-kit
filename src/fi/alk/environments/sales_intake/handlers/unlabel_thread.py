def handle(args, db):
    thread_id = (args.get("threadId") or "").strip() if isinstance(args.get("threadId"), str) else ""
    # Labels organise the desk's own sales@ mailbox: staff work it, a prospect does not.
    if _caller(db)[0] != "staff":
        raise ToolError("Permission denied: the sales@ mailbox's labels are the desk's own; only BrightDesk staff in this conversation can change them.", category="permission_denied")
    if not thread_id:
        raise ToolError("threadId is required", category="invalid_input")
    label_ids = args.get("labelIds")
    if not isinstance(label_ids, list) or not label_ids or not all(isinstance(x, str) and x for x in label_ids):
        raise ToolError("labelIds must be a non-empty list of label IDs", category="invalid_input")

    t = db.one("SELECT * FROM gmail_threads WHERE id = ?", (thread_id,))
    if t is None:
        raise ToolError(f"Thread {thread_id!r} not found", category="not_found")
    if t["is_restricted"]:
        raise ToolError(f"Access to thread {thread_id!r} is restricted", category="permission_denied")

    for lid in label_ids:
        if db.one("SELECT id FROM gmail_labels WHERE id = ?", (lid,)) is None:
            raise ToolError(f"Label {lid!r} does not exist; labelIds takes label IDs, not names", category="not_found")
    for lid in label_ids:
        db.execute("DELETE FROM gmail_thread_labels WHERE thread_id = ? AND label_id = ?", (thread_id, lid))
    return {}


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
