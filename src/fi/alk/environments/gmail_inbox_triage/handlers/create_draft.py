def handle(args, db):
    thread_id = (args.get("thread_id") or "").strip()
    recipient = (args.get("recipient") or "").strip()
    subject = (args.get("subject") or "").strip()
    body = (args.get("body") or "").strip()
    cc = (args.get("cc") or "").strip()
    bcc = (args.get("bcc") or "").strip()
    
    if not thread_id:
        raise ToolError("thread_id is required", category="invalid_input")
    if not recipient:
        raise ToolError("recipient is required", category="invalid_input")
    if not subject:
        raise ToolError("subject is required", category="invalid_input")
    if not body:
        raise ToolError("body is required", category="invalid_input")
        
    t = db.one("SELECT * FROM threads WHERE id = ?", (thread_id,))
    if t is None:
        raise ToolError(f"Thread {thread_id!r} not found", category="not_found")
    if t["is_restricted"]:
        raise ToolError(f"Access to thread {thread_id!r} is restricted", category="permission_denied")
        
    existing_human_draft = db.one("SELECT * FROM drafts WHERE thread_id = ? AND is_human_edited = TRUE", (thread_id,))
    if existing_human_draft:
        raise ToolError(f"A human-edited draft ({existing_human_draft['id']}) already exists for thread {thread_id!r}. Cannot overwrite without human confirmation.", category="conflict")
        
    clock_row = db.one("SELECT now FROM clock")
    now = clock_row["now"] if clock_row else "2026-10-26T10:00:00Z"
    
    count_row = db.one("SELECT COUNT(*) as c FROM drafts")
    count = count_row["c"] if count_row else 0
    draft_id = f"D-{1001 + count}"
    
    db.execute(
        "INSERT INTO drafts (id, thread_id, recipient, cc, bcc, subject, body, is_human_edited, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, FALSE, ?, ?)",
        (draft_id, thread_id, recipient, cc, bcc, subject, body, now, now)
    )
    
    return {
        "created_at": now,
        "draft_id": draft_id,
        "recipient": recipient,
        "subject": subject,
        "thread_id": thread_id
    }
