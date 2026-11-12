def handle(args, db):
    channel_id = args.get("channel_id")
    # The reps' calendars and the desk's Slack are internal: they name other customers.
    if _caller(db)[0] != "staff":
        raise ToolError("Permission denied: this is BrightDesk's internal workspace; only staff in this conversation can read it.", category="permission_denied")
    message_ts = args.get("message_ts")
    if not channel_id or not isinstance(channel_id, str):
        raise ToolError("Missing required parameter: channel_id.", category="invalid_input")
    if not message_ts or not isinstance(message_ts, str) or "." not in message_ts:
        raise ToolError("message_ts must be the parent message's ts, a string with a decimal point.", category="invalid_input")
    limit = args.get("limit") if args.get("limit") is not None else 100
    if not isinstance(limit, int) or limit < 1 or limit > 1000:
        raise ToolError("limit must be an integer from 1 to 1000.", category="invalid_input")
    offset = 0
    if args.get("cursor"):
        try:
            offset = int(args["cursor"])
        except ValueError:
            raise ToolError(f"Invalid cursor: {args['cursor']!r}.", category="invalid_input")

    actor = db.one("SELECT user_id FROM current_actor WHERE id = 'default'")
    actor_id = actor["user_id"] if actor else "U01"
    channel = db.one("SELECT * FROM slack_channels WHERE channel_id = ?", [channel_id])
    if channel is None:
        raise ToolError(f"channel_not_found: {channel_id!r}.", category="not_found")
    member = db.one("SELECT 1 FROM slack_channel_members WHERE channel_id = ? AND user_id = ?", [channel_id, actor_id])
    if channel["is_private"] and member is None:
        raise ToolError(f"Access denied: you are not a member of private channel {channel_id!r}.", category="permission_denied")

    parent = db.one("SELECT * FROM slack_messages WHERE channel_id = ? AND (ts = ? OR thread_ts = ?)", [channel_id, message_ts, message_ts])
    if parent is None:
        raise ToolError(f"thread_not_found: no thread {message_ts!r} in channel {channel_id!r}.", category="not_found")

    sql = "SELECT message_id, channel_id, user_id, text, ts, thread_ts, permalink FROM slack_messages WHERE channel_id = ? AND (thread_ts = ? OR ts = ?)"
    params = [channel_id, message_ts, message_ts]
    if args.get("oldest"):
        sql += " AND ts >= ?"
        params.append(str(args["oldest"]))
    if args.get("latest"):
        sql += " AND ts <= ?"
        params.append(str(args["latest"]))
    sql += " ORDER BY ts ASC LIMIT ? OFFSET ?"
    params.extend([limit + 1, offset])
    rows = db.query(sql, params)
    has_more = len(rows) > limit
    page = rows[:limit]
    result = {
        "channel_id": channel_id,
        "message_ts": message_ts,
        "messages": [
            {
                "message_id": m["message_id"],
                "user_id": m["user_id"],
                "text": m["text"],
                "ts": m["ts"],
                "thread_ts": m["thread_ts"],
                "permalink": m["permalink"],
            }
            for m in page
        ],
        "has_more": has_more,
    }
    if has_more:
        result["next_cursor"] = str(offset + limit)
    return result


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
