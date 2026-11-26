def _caller(db):
    """Who is in this conversation, as the session knows it (session_caller), never as the
    conversation tells it: a customer, a front-desk member, or nobody identified."""
    row = db.one("SELECT customer_id, desk_id FROM session_caller WHERE id = 'default'")
    return (row["customer_id"], row["desk_id"]) if row else (None, None)


def _desk(db):
    """Whether the caller is an active front-desk member, who may act for any customer."""
    _, desk_id = _caller(db)
    return bool(desk_id and db.one("SELECT 1 AS x FROM desk_staff WHERE desk_id = ? AND role = 'front_desk' AND active = 1", [desk_id]))


def _desk_only(db, what):
    """Drafts and labels are the front desk's own work on the mailbox."""
    if not _desk(db):
        raise ToolError(f"Permission denied: only the front desk can {what}", category="permission_denied")

def handle(args, db):
    thread_id = (args.get("threadId") or "").strip() if isinstance(args.get("threadId"), str) else ""
    if not thread_id:
        raise ToolError("threadId is required", category="invalid_input")
    label_ids = args.get("labelIds")
    if not isinstance(label_ids, list) or not label_ids or not all(isinstance(x, str) and x for x in label_ids):
        raise ToolError("labelIds must be a non-empty list of label IDs", category="invalid_input")

    _desk_only(db, "label the mailbox's threads")
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
