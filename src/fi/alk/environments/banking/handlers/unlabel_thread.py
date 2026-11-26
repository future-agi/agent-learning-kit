_BANK = "@apexhorizonbank.com"


def _addresses(*fields):
    """The complete mailbox addresses in address fields ("a@x, Name <b@y>"), lower-cased. Only a
    whole address is one: a display name, or a longer address that contains it, is not."""
    from email.utils import getaddresses

    return {address.strip().lower() for _, address in getaddresses([f for f in fields if f]) if "@" in address}


def _caller_email(db):
    """The email of the customer signed in to this conversation, set by the session."""
    row = db.one("SELECT c.email FROM session_caller s JOIN customers c ON c.customer_id = s.customer_id WHERE s.id = 'default'")
    return row["email"].strip().lower() if row else None


def _colleague(db):
    """Whether the person in this conversation, as the session knows them, is one of the bank's
    active support colleagues, who work the whole mailbox."""
    return db.one("SELECT 1 AS x FROM session_caller s JOIN support_staff p ON p.staff_id = s.staff_id WHERE s.id = 'default' AND p.active = 1") is not None


def _people(*fields):
    """The non-bank addresses in comma-separated address fields."""
    return {address for address in _addresses(*fields) if not address.endswith(_BANK)}


def _yours(db, thread_id):
    """The support mailbox holds every customer's email; a customer's thread is opened only for that
    customer or a support colleague, and one with no customer on it only for a colleague."""
    if _colleague(db):
        return True
    people = set()
    for m in db.query("SELECT sender, recipient, cc FROM gmail_messages WHERE thread_id = ?", (thread_id,)):
        people |= _people(m["sender"], m["recipient"], m["cc"])
    for d in db.query("SELECT recipient, cc, bcc FROM gmail_drafts WHERE thread_id = ?", (thread_id,)):
        people |= _people(d["recipient"], d["cc"], d["bcc"])
    return people == {_caller_email(db)}


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
        raise ToolError(f"Thread {thread_id!r} is not the mail of the customer in this conversation", category="permission_denied")

    for lid in label_ids:
        if db.one("SELECT id FROM gmail_labels WHERE id = ?", (lid,)) is None:
            raise ToolError(f"Label {lid!r} does not exist; labelIds takes label IDs, not names", category="not_found")
    for lid in label_ids:
        db.execute("DELETE FROM gmail_thread_labels WHERE thread_id = ? AND label_id = ?", (thread_id, lid))
    return {}
