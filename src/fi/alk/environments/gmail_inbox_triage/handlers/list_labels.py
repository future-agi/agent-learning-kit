def handle(args, db):
    page_size = args.get("pageSize")
    if page_size is not None and (not isinstance(page_size, int) or page_size < 1):
        raise ToolError("pageSize must be a positive integer", category="invalid_input")
    offset = 0
    if args.get("pageToken"):
        try:
            offset = int(args["pageToken"])
        except ValueError:
            raise ToolError(f"Invalid pageToken: {args['pageToken']!r}", category="invalid_input")

    # User-defined labels only; the system label IDs (INBOX, UNREAD, STARRED, IMPORTANT, SENT,
    # DRAFT, SPAM, TRASH, CHAT) are known without listing them.
    labels = []
    for label in db.query("SELECT * FROM labels WHERE type = 'user' ORDER BY name ASC"):
        threads = [row["thread_id"] for row in db.query("SELECT thread_id FROM thread_labels WHERE label_id = ?", (label["id"],))]
        unread = [
            thread for thread in threads
            if db.one("SELECT 1 FROM thread_labels WHERE thread_id = ? AND label_id = 'UNREAD'", (thread,))
        ]
        labels.append({"labelId": label["id"], "name": label["name"], "threadsTotal": len(threads), "threadsUnread": len(unread)})

    paged = labels[offset : offset + page_size] if page_size else labels[offset:]
    result = {"labels": paged}
    if page_size and offset + page_size < len(labels):
        result["nextPageToken"] = str(offset + page_size)
    return result
