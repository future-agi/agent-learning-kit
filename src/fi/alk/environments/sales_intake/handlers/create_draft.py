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

    clock_row = db.one("SELECT now FROM clock")
    now = clock_row["now"] if clock_row else "2026-10-26T10:00:00Z"

    # A draft is the desk writing on someone's behalf: staff draft from the sales@ mailbox; a
    # prospect only to themselves and only on their own correspondence, so a draft can neither
    # land in another person's thread nor echo its subject back.
    kind, who = _caller(db)
    if kind is None:
        raise ToolError("Permission denied: nobody is signed in to this conversation, so nothing can be drafted.", category="permission_denied")
    if kind == "contact" and any(a.strip().lower() != who["email_normalized"] for a in to + cc + bcc):
        raise ToolError(f"Permission denied: {_named(kind, who)} can only have the desk draft to their own address.", category="permission_denied")

    if reply_to:
        original = db.one("SELECT * FROM gmail_messages WHERE id = ?", (reply_to,))
        if original is None:
            raise ToolError(f"Message {reply_to!r} not found", category="not_found")
        thread_id = original["thread_id"]
        if not _sees(db, thread_id):
            raise ToolError(f"Permission denied: message {reply_to!r} is not the correspondence of the person in this conversation.", category="permission_denied")
        t = db.one("SELECT * FROM gmail_threads WHERE id = ?", (thread_id,))
        if t and t["is_restricted"]:
            raise ToolError(f"Access to thread {thread_id!r} is restricted", category="permission_denied")
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

    # No outreach to a person who opted out, not even a draft waiting to be sent.
    for address in to + cc + bcc:
        opted = db.one("SELECT contact_id FROM contacts WHERE email_normalized = ? AND consent_status = 'opted_out'", (address.strip().lower(),))
        if opted:
            raise ToolError(f"{address} belongs to contact {opted['contact_id']}, who opted out; no message to them may be drafted.", category="policy_blocked")

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
