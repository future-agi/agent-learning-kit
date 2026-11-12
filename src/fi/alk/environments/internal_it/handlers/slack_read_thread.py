def handle(args, db):
    channel_id = args.get("channel_id")
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
    caller = _may_relay(db, channel_id)

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
                "text": _redact(db, caller, m["text"]),
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


def _reader(db):
    """Who the desk reads Slack for, and the channels it may pass on to them. The desk's own
    membership decides what Slack lets it open; the person signed in decides what it may relay.
    IT operations staff get every channel the desk can read: the desk reads its shared channels
    (#it-access, #it-ops) on their behalf. Anyone else gets only the channels they are a member of
    themselves, which every employee is for #it-access. Nobody signed in, or an inactive employee,
    gets none. Returns (caller, channel ids or None for every channel the desk can read)."""
    row = db.one("SELECT employee_id FROM session_caller WHERE id = 'default'")
    caller = row["employee_id"] if row else None
    emp = db.one("SELECT * FROM employees WHERE employee_id = ?", [caller]) if caller else None
    if not emp or emp["status"] != "active":
        return caller, set()
    if emp["department"] == "it_operations":
        return caller, None
    user = db.one("SELECT user_id FROM slack_users WHERE lower(email) = lower(?)", [emp["email"]])
    if not user:
        return caller, set()
    return caller, {r["channel_id"] for r in db.query("SELECT channel_id FROM slack_channel_members WHERE user_id = ?", [user["user_id"]])}


def _may_relay(db, channel_id):
    """Refuse a channel the person signed in may not read through the desk."""
    caller, channels = _reader(db)
    if channels is not None and channel_id not in channels:
        raise ToolError(f"Permission denied: the person signed in to this conversation ({caller or 'nobody'}) is not a member of {channel_id!r}, so the desk does not read it for them.", category="permission_denied")
    return caller


def _redact(db, caller, text):
    """An approval token posted in a channel is still that request's token: it is shown only to
    someone get_approval_status would show it to (the requester, their manager, the request's
    approvers, IT operations) and withheld from everyone else who can read the channel."""
    import re
    for token in sorted(set(re.findall(r"\bTOK-[A-Za-z0-9-]+\b", text or ""))):
        approval = db.one("SELECT * FROM approvals WHERE token = ?", [token])
        if approval is None:
            continue
        req = db.one("SELECT * FROM access_requests WHERE request_id = ?", [approval["request_id"]]) or {}
        me = db.one("SELECT department, status FROM employees WHERE employee_id = ?", [caller]) if caller else None
        holder = db.one("SELECT manager_id FROM employees WHERE employee_id = ?", [approval["employee_id"]])
        sees = bool(caller) and bool(me) and me["status"] == "active" and (
            caller in (approval["employee_id"], approval["approver_id"], req.get("approver_id"), req.get("approved_by"))
            or me["department"] == "it_operations"
            or (holder is not None and holder["manager_id"] == caller)
        )
        if not sees:
            text = text.replace(token, "[approval token withheld]")
    return text
