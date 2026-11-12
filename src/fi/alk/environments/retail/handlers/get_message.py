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
    if not _yours(db, m["thread_id"]):
        raise ToolError(f"Message {message_id!r} is another customer's; it is opened only with that customer", category="permission_denied")

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
