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
    label_ids = args.get("labelIds")
    if not isinstance(label_ids, list) or not label_ids or not all(isinstance(x, str) and x for x in label_ids):
        raise ToolError("labelIds must be a non-empty list of label IDs", category="invalid_input")

    t = db.one("SELECT * FROM gmail_threads WHERE id = ?", (thread_id,))
    if t is None:
        raise ToolError(f"Thread {thread_id!r} not found", category="not_found")
    if t["is_restricted"]:
        raise ToolError(f"Access to thread {thread_id!r} is restricted", category="permission_denied")
    if _withheld(db, thread_id):
        # Filing someone's mail is acting on it: only for a patient this conversation may see.
        raise ToolError(f"Thread {thread_id!r} is about a patient who is not verified in this conversation; verify the patient first.", category="permission_denied")

    for lid in label_ids:
        if lid in ("TRASH", "SPAM"):
            raise ToolError(f"Use apply_sensitive_thread_label to add {lid}; it is not available in this mailbox", category="invalid_input")
        if db.one("SELECT id FROM gmail_labels WHERE id = ?", (lid,)) is None:
            raise ToolError(f"Label {lid!r} does not exist; labelIds takes label IDs, not names", category="not_found")

    for lid in label_ids:
        if not db.one("SELECT 1 FROM gmail_thread_labels WHERE thread_id = ? AND label_id = ?", (thread_id, lid)):
            db.execute("INSERT INTO gmail_thread_labels (thread_id, label_id) VALUES (?, ?)", (thread_id, lid))
    return {}
