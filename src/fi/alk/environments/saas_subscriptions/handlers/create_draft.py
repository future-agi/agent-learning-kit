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

    if reply_to:
        original = db.one("SELECT * FROM gmail_messages WHERE id = ?", (reply_to,))
        if original is None:
            raise ToolError(f"Message {reply_to!r} not found", category="not_found")
        thread_id = original["thread_id"]
        t = db.one("SELECT * FROM gmail_threads WHERE id = ?", (thread_id,))
        if t and t["is_restricted"]:
            raise ToolError(f"Access to thread {thread_id!r} is restricted", category="permission_denied")
        # Replying is reading: a draft can be put only on a thread the caller may see.
        if not _may_read_thread(db, thread_id):
            raise ToolError(f"Message {reply_to!r} is on mail you may not see: its thread is filed under another tenant, or no one is signed in", category="permission_denied")
        if not subject:
            subject = original["subject"] if original["subject"].lower().startswith("re:") else f"Re: {original['subject']}"
    else:
        # A new conversation: Gmail starts a thread for a draft that replies to nothing. It is filed
        # under the caller's tenant, or, for staff, the tenant of the customer it is addressed to,
        # so the same people who may read that tenant's mail see the draft.
        caller = db.one("SELECT actor_id FROM session_caller WHERE id = 'default'")
        caller = caller["actor_id"] if caller else None
        if not caller:
            raise ToolError("No one is signed in to this conversation, so there is no one to draft for.", category="permission_denied")
        own = db.one("SELECT tenant_id FROM users WHERE actor_id = ?", [caller])
        filed = own["tenant_id"] if own else next(
            (u["tenant_id"] for a in to for u in db.query("SELECT tenant_id FROM users WHERE lower(email) = ?", [a.lower()])), None
        )
        count = db.one("SELECT COUNT(*) AS c FROM gmail_threads")["c"]
        thread_id = f"T-{count + 1:02d}"
        while db.one("SELECT 1 FROM gmail_threads WHERE id = ?", (thread_id,)):
            count += 1
            thread_id = f"T-{count + 1:02d}"
        db.execute(
            "INSERT INTO gmail_threads (id, subject, snippet, history_id, is_restricted, updated_at) VALUES (?, ?, ?, ?, FALSE, ?)",
            (thread_id, subject, (body or html_body)[:80], f"H-{2000 + count}", now),
        )
        if filed:
            db.execute("INSERT INTO gmail_thread_tenants (thread_id, tenant_id) VALUES (?, ?)", (thread_id, filed))

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


def _may_read_thread(db, thread_id):
    """Whether the person signed in to the conversation (session_caller) may see this mailbox thread.
    The billing mailbox holds every tenant's mail, so the same boundary as the billing tools applies:
    each thread is filed under the tenant it concerns (gmail_thread_tenants). A tenant's billing
    admin sees that tenant's threads; a member sees only threads they wrote or were sent; Cloudnest
    staff in a billing role (billing lead, support, operations, finance) see every thread, an account
    manager only threads of the tenants assigned to them; nobody signed in sees nothing. A thread
    restricted by the mailbox ACL stays closed to everyone, as before."""
    row = db.one("SELECT actor_id FROM session_caller WHERE id = 'default'")
    caller = row["actor_id"] if row else None
    if not caller:
        return False
    filed = db.one("SELECT tenant_id FROM gmail_thread_tenants WHERE thread_id = ?", [thread_id])
    tenant = filed["tenant_id"] if filed else None
    user = db.one("SELECT * FROM users WHERE actor_id = ?", [caller])
    if user is not None:
        if tenant != user["tenant_id"]:
            return False
        if user["role"] == "billing_admin":
            return True
        email = user["email"].lower()
        rows = db.query("SELECT sender, recipient, cc FROM gmail_messages WHERE thread_id = ?", [thread_id])
        rows += db.query("SELECT '' AS sender, recipient, cc FROM gmail_drafts WHERE thread_id = ?", [thread_id])
        return any(email in f"{m['sender']},{m['recipient']},{m['cc'] or ''}".lower() for m in rows)
    staff = db.one("SELECT role FROM slack_users WHERE user_id = ?", [caller])
    if staff is None:
        return False
    if staff["role"] in ("billing_lead", "billing_support", "billing_operations", "finance"):
        return True
    return tenant is not None and db.one(
        "SELECT 1 FROM account_assignments WHERE tenant_id = ? AND user_id = ?", [tenant, caller]
    ) is not None
