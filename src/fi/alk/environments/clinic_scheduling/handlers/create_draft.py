def _withheld(db, thread_id):
    """The patient a mailbox thread is about whom this conversation may not see yet, or None. The
    appointments mailbox holds patients' health details: a thread linked to a patient is shown only
    once that patient is verified by the person talking, who must be them or hold recorded
    guardian or proxy authority for them."""
    row = db.one("SELECT caller_id FROM session_caller WHERE id = 'default'")
    caller = row["caller_id"] if row else None
    for link in db.query("SELECT patient_id FROM gmail_thread_patients WHERE thread_id = ?", (thread_id,)):
        pid = link["patient_id"]
        entitled = caller is not None and (caller == pid or db.one(
            "SELECT patient_id FROM caller_access WHERE caller_id = ? AND patient_id = ?", (caller, pid)) is not None)
        if not entitled or not db.one("SELECT patient_id FROM verified_patients WHERE patient_id = ?", (pid,)):
            return pid
    return None


def _handle(args, db):
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
        if _withheld(db, thread_id):
            raise ToolError(f"Thread {thread_id!r} is about a patient who is not verified in this conversation; verify the patient first.", category="permission_denied")
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


_WRITES = ("gmail_drafts", "gmail_threads")

def _all_or_nothing(db, run):
    """Run the call so that a failed call changes nothing. A handler gets no transaction of its own,
    so every table this tool writes is held first and put back if the call raises, whether a write
    failed partway or the call refused after writing. Only a fault the world simulates on purpose
    (an error marked keep_writes: a rival booking, a commit whose reply was lost, a fault used up)
    keeps what it wrote."""
    held = {table: db.query(f"SELECT * FROM {table}") for table in _WRITES}
    try:
        return run()
    except Exception as failed:
        if not getattr(failed, "keep_writes", False):
            for table, rows in held.items():
                if _same(db.query(f"SELECT * FROM {table}"), rows):
                    continue
                db.execute(f"DELETE FROM {table}")
                for row in rows:
                    cols = list(row)
                    db.execute(
                        f"INSERT INTO {table} ({', '.join(chr(34) + c + chr(34) for c in cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [row[c] for c in cols],
                    )
        raise

def _same(rows, held):
    """Whether a table still holds exactly the rows it held, in any order: only a table the call
    changed is rewritten."""
    import json

    def key(row):
        return json.dumps(row, sort_keys=True, default=str)

    return sorted(map(key, rows)) == sorted(map(key, held))


def handle(args, db):
    return _all_or_nothing(db, lambda: _handle(args, db))
