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
    if _withheld(db, thread_id):
        raise ToolError(f"Thread {thread_id!r} is about a patient who is not verified in this conversation; verify the patient (full name and date of birth) first.", category="permission_denied")

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
