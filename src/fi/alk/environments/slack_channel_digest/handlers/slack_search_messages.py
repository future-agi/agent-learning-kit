def handle(args, db):
    if "actor_id" not in args or not args["actor_id"]:
        raise ToolError("Missing required parameter: actor_id.")
    if "channel_id" not in args or not args["channel_id"]:
        raise ToolError("Missing required parameter: channel_id.")
    if "limit" not in args or args["limit"] is None:
        raise ToolError("Missing required parameter: limit.")
    if "cursor" not in args or args["cursor"] is None:
        raise ToolError("Missing required parameter: cursor.")
        
    actor_id = args["actor_id"]
    channel_id = args["channel_id"]
    query = args.get("query")
    
    try:
        limit_val = int(args["limit"])
        if limit_val <= 0:
            raise ToolError("limit must be positive integer.")
    except (ValueError, TypeError):
        raise ToolError("limit must be an integer.")
        
    try:
        offset = int(args["cursor"])
        if offset < 0:
            raise ToolError("cursor must be non-negative integer string.")
    except (ValueError, TypeError):
        raise ToolError(f"Invalid cursor format: {args['cursor']!r}.")
        
    user = db.one("SELECT * FROM users WHERE user_id = ?", [actor_id])
    if user is None:
        raise ToolError(f"User {actor_id!r} not found.")
        
    channel = db.one("SELECT * FROM channels WHERE channel_id = ?", [channel_id])
    if channel is None:
        raise ToolError(f"Channel {channel_id!r} not found.")
        
    membership = db.one("SELECT * FROM channel_members WHERE channel_id = ? AND user_id = ?", [channel_id, actor_id])
    if channel["is_private"] and membership is None:
        raise ToolError(f"Access denied: Actor {actor_id!r} is not a member of private channel {channel_id!r}.")
        
    sql = "SELECT message_id, channel_id, user_id, text, ts, thread_ts, permalink FROM messages WHERE channel_id = ?"
    params = [channel_id]
    
    if query:
        sql += " AND LOWER(text) LIKE ?"
        params.append(f"%{query.lower()}%")
        
    sql += " ORDER BY ts ASC LIMIT ? OFFSET ?"
    params.extend([limit_val + 1, offset])
    
    rows = db.query(sql, params)
    
    has_more = len(rows) > limit_val
    messages = rows[:limit_val]
    next_cursor = str(offset + limit_val) if has_more else None
    
    return {
        "channel_id": channel_id,
        "messages": [
            {
                "message_id": m["message_id"],
                "user_id": m["user_id"],
                "text": m["text"],
                "ts": m["ts"],
                "thread_ts": m["thread_ts"],
                "permalink": m["permalink"]
            }
            for m in messages
        ],
        "has_more": has_more,
        "next_cursor": next_cursor,
        "total_returned": len(messages)
    }
