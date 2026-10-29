def handle(args, db):
    q = (args.get("q") or "").strip()
    label_ids = list(args.get("label_ids") or [])
    max_results = args.get("max_results") or 10
    if not isinstance(max_results, int) or max_results < 1:
        raise ToolError("max_results must be a positive integer", category="invalid_input")
    
    offset = 0
    page_token = args.get("page_token")
    if page_token:
        try:
            offset = int(page_token)
        except ValueError:
            raise ToolError(f"Invalid page_token: {page_token!r}", category="invalid_input")
            
    search_terms = []
    if q:
        for part in q.split():
            if part.lower().startswith("label:"):
                label_ids.append(part.split(":", 1)[1].upper())
            elif part.lower() == "is:unread":
                label_ids.append("UNREAD")
            elif part.lower() == "is:inbox":
                label_ids.append("INBOX")
            else:
                search_terms.append(part)
                
    all_threads = db.query("SELECT * FROM threads WHERE is_restricted = FALSE ORDER BY updated_at DESC")
    matched = []
    for t in all_threads:
        t_id = t["id"]
        t_labels = [row["label_id"] for row in db.query("SELECT label_id FROM thread_labels WHERE thread_id = ?", (t_id,))]
        
        if label_ids:
            if not all(lid in t_labels for lid in label_ids):
                continue
                
        if search_terms:
            t_msgs = db.query("SELECT subject, body, sender, recipient FROM messages WHERE thread_id = ?", (t_id,))
            full_text = (t["subject"] + " " + t["snippet"] + " " + " ".join(m["subject"] + " " + m["body"] + " " + m["sender"] for m in t_msgs)).lower()
            if not all(term.lower() in full_text for term in search_terms):
                continue
                
        matched.append({
            "id": t["id"],
            "snippet": t["snippet"],
            "history_id": t["history_id"],
            "label_ids": t_labels
        })
        
    paged = matched[offset:offset + max_results]
    next_token = str(offset + max_results) if (offset + max_results) < len(matched) else None
    
    return {
        "threads": paged,
        "next_page_token": next_token,
        "result_size_estimate": len(matched)
    }
