_STORE = "@northgate-outfitters.example"


def _caller_email(db):
    """The email of the customer signed in to this conversation, set by the session."""
    row = db.one("SELECT c.email FROM session_caller s JOIN customers c ON c.customer_id = s.customer_id WHERE s.id = 'default'")
    return row["email"].strip().lower() if row else None


def _people(*fields):
    """The non-store addresses in comma-separated address fields."""
    found = set()
    for field in fields:
        for address in (field or "").split(","):
            address = address.strip().lower()
            if address and not address.endswith(_STORE):
                found.add(address)
    return found


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
    def addresses(name):
        value = args.get(name)
        if value is None:
            return []
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise ToolError(f"{name} must be a list of email addresses", category="invalid_input")
        for address in value:
            if "<" in address or " " in address.strip() or "@" not in address:
                raise ToolError(f"{name}: {address!r} is not a plain email address ('Name <email>' is not supported)", category="invalid_input")
        return [x.strip() for x in value]

    to, cc, bcc = addresses("to"), addresses("cc"), addresses("bcc")
    if args.get("attachments"):
        raise ToolError("Creating drafts with attachments is not supported yet", category="invalid_input")
    subject = args.get("subject") or ""
    body = args.get("body") or ""
    html_body = args.get("htmlBody") or ""
    reply_to = args.get("replyToMessageId")
    if reply_to is not None and not isinstance(reply_to, str):
        raise ToolError("replyToMessageId must be a message ID", category="invalid_input")

    # A draft goes to the customer in the conversation (or the store), never to someone else.
    others = _people(",".join(to), ",".join(cc), ",".join(bcc)) - {_caller_email(db)}
    if others:
        raise ToolError(f"A draft goes only to the customer in this conversation; {sorted(others)} are not them", category="permission_denied")

    clock_row = db.one("SELECT now FROM clock")
    now = clock_row["now"] if clock_row else "2026-10-26T10:00:00Z"

    if reply_to:
        original = db.one("SELECT * FROM gmail_messages WHERE id = ?", (reply_to,))
        if original is None:
            raise ToolError(f"Message {reply_to!r} not found", category="not_found")
        thread_id = original["thread_id"]
        t = db.one("SELECT * FROM gmail_threads WHERE id = ?", (thread_id,))
        if t and t["is_restricted"]:
            raise ToolError(f"Access to thread {thread_id!r} is restricted", category="permission_denied")
        if not _yours(db, thread_id):
            raise ToolError(f"Thread {thread_id!r} is another customer's; a reply on it is drafted only with that customer", category="permission_denied")
        if not subject:
            subject = original["subject"] if original["subject"].lower().startswith("re:") else f"Re: {original['subject']}"
    else:
        # A new conversation: Gmail starts a thread for a draft that replies to nothing.
        count = db.one("SELECT COUNT(*) AS c FROM gmail_threads")["c"]
        thread_id = f"T-{count + 1:02d}"
        while db.one("SELECT 1 FROM gmail_threads WHERE id = ?", (thread_id,)):
            count += 1
            thread_id = f"T-{count + 1:02d}"
        db.execute(
            "INSERT INTO gmail_threads (id, subject, snippet, history_id, is_restricted, updated_at) VALUES (?, ?, ?, ?, FALSE, ?)",
            (thread_id, subject, (body or html_body)[:80], f"H-{2000 + count}", now),
        )

    existing_human_draft = db.one("SELECT * FROM gmail_drafts WHERE thread_id = ? AND is_human_edited = TRUE", (thread_id,))
    if existing_human_draft:
        raise ToolError(
            f"A human-edited draft ({existing_human_draft['id']}) already exists for thread {thread_id!r}. Cannot overwrite without human confirmation.",
            category="conflict",
        )

    count = db.one("SELECT COUNT(*) AS c FROM gmail_drafts")["c"]
    draft_id = f"D-{1001 + count}"
    db.execute(
        "INSERT INTO gmail_drafts (id, thread_id, recipient, cc, bcc, subject, body, is_human_edited, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, FALSE, ?, ?)",
        (draft_id, thread_id, ",".join(to), ",".join(cc), ",".join(bcc), subject, body or html_body, now, now),
    )
    return {
        "id": draft_id,
        "subject": subject,
        "threadId": thread_id,
        "toRecipients": to,
        "ccRecipients": cc,
        "bccRecipients": bcc,
        "plaintextBody": body,
        "htmlBody": html_body,
        "date": now[:10],
    }
