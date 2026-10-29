def handle(args, db):
    thread_id = (args.get("thread_id") or "").strip()
    if not thread_id:
        raise ToolError("thread_id is required", category="invalid_input")
        
    t = db.one("SELECT * FROM threads WHERE id = ?", (thread_id,))
    if t is None:
        raise ToolError(f"Thread {thread_id!r} not found", category="not_found")
    if t["is_restricted"]:
        raise ToolError(f"Access to thread {thread_id!r} is restricted", category="permission_denied")
        
    add_label_ids = args.get("add_label_ids") or []
    remove_label_ids = args.get("remove_label_ids") or []
    
    if not add_label_ids and not remove_label_ids:
        raise ToolError("At least one label ID to add or remove must be provided", category="invalid_input")
        
    for lid in add_label_ids:
        lbl = db.one("SELECT id FROM labels WHERE id = ?", (lid,))
        if lbl is None:
            raise ToolError(f"Label {lid!r} does not exist", category="not_found")
            
    for lid in add_label_ids:
        exists = db.one("SELECT 1 FROM thread_labels WHERE thread_id = ? AND label_id = ?", (thread_id, lid))
        if not exists:
            db.execute("INSERT INTO thread_labels (thread_id, label_id) VALUES (?, ?)", (thread_id, lid))
            
    for lid in remove_label_ids:
        db.execute("DELETE FROM thread_labels WHERE thread_id = ? AND label_id = ?", (thread_id, lid))
        
    current = [row["label_id"] for row in db.query("SELECT label_id FROM thread_labels WHERE thread_id = ?", (thread_id,))]
    return {"thread_id": thread_id, "label_ids": current}
