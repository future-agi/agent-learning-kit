_STORE = "@northgate-outfitters.example"


def _addresses(*fields):
    """The complete mailbox addresses in address fields ("a@x, Name <b@y>"), lower-cased. Only a
    whole address is one: a display name, or a longer address that contains it, is not."""
    from email.utils import getaddresses

    return {address.strip().lower() for _, address in getaddresses([f for f in fields if f]) if "@" in address}


def _caller_email(db):
    """The email of the customer signed in to this conversation, set by the session."""
    row = db.one("SELECT c.email FROM session_caller s JOIN customers c ON c.customer_id = s.customer_id WHERE s.id = 'default'")
    return row["email"].strip().lower() if row else None


def _people(*fields):
    """The non-store addresses in comma-separated address fields."""
    return {address for address in _addresses(*fields) if not address.endswith(_STORE)}


def _yours(db, thread_id):
    """The support mailbox holds every customer's email; the desk opens a customer's thread only
    while that customer is the one in the conversation. A thread with no customer on it is the
    store's own."""
    people = set()
    for m in db.query("SELECT sender, recipient, cc FROM gmail_messages WHERE thread_id = ?", (thread_id,)):
        people |= _people(m["sender"], m["recipient"], m["cc"])
    for d in db.query("SELECT recipient, cc, bcc FROM gmail_drafts WHERE thread_id = ?", (thread_id,)):
        people |= _people(d["recipient"], d["cc"], d["bcc"])
    return not people or people <= {_caller_email(db)}


def handle(args, db):
    thread_id = (args.get("threadId") or "").strip() if isinstance(args.get("threadId"), str) else ""
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
    if not _yours(db, thread_id):
        raise ToolError(f"Thread {thread_id!r} is another customer's; it is opened only with that customer", category="permission_denied")

    for lid in label_ids:
        if lid in ("TRASH", "SPAM"):
            raise ToolError(f"Use apply_sensitive_thread_label to add {lid}; it is not available in this mailbox", category="invalid_input")
        if db.one("SELECT id FROM gmail_labels WHERE id = ?", (lid,)) is None:
            raise ToolError(f"Label {lid!r} does not exist; labelIds takes label IDs, not names", category="not_found")

    for lid in label_ids:
        if not db.one("SELECT 1 FROM gmail_thread_labels WHERE thread_id = ? AND label_id = ?", (thread_id, lid)):
            db.execute("INSERT INTO gmail_thread_labels (thread_id, label_id) VALUES (?, ?)", (thread_id, lid))
    return {}
