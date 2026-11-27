def handle(args, db):
    import re
    from datetime import datetime, timezone

    keywords = args.get("keywords") or []
    filters = (args.get("filters") or "").strip()
    if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
        raise ToolError("keywords must be a list of single words or quoted phrases.", category="invalid_input")
    if not keywords and not filters:
        raise ToolError("Give at least one of keywords or filters.", category="invalid_input")
    limit = args.get("limit") if args.get("limit") is not None else 20
    if not isinstance(limit, int) or limit < 1 or limit > 20:
        raise ToolError("limit must be an integer from 1 to 20.", category="invalid_input")
    offset = 0
    if args.get("cursor"):
        try:
            offset = int(args["cursor"])
        except ValueError:
            raise ToolError(f"Invalid cursor: {args['cursor']!r}.", category="invalid_input")
    sort = args.get("sort") or "score"
    if sort not in ("score", "timestamp"):
        raise ToolError("sort must be 'score' or 'timestamp'.", category="invalid_input")
    sort_dir = args.get("sort_dir") or "desc"
    if sort_dir not in ("asc", "desc"):
        raise ToolError("sort_dir must be 'asc' or 'desc'.", category="invalid_input")
    types = [t.strip() for t in (args.get("channel_types") or "public_channel,private_channel,mpim,im").split(",") if t.strip()]
    for t in types:
        if t not in ("public_channel", "private_channel", "mpim", "im"):
            raise ToolError(f"channel_types: {t!r} is not one of public_channel, private_channel, mpim, im.", category="invalid_input")

    # Slack search modifiers this workspace answers: in:, from:, after:, before:.
    channels_in, from_users, after, before = [], [], None, None
    for modifier in filters.split():
        m = re.match(r"^(in|from|after|before):(.+)$", modifier)
        if not m:
            raise ToolError(f"Unsupported search modifier {modifier!r}: this workspace answers in:, from:, after: and before:.", category="invalid_input")
        kind, value = m.groups()
        if kind == "in":
            ref = value.strip("<>#@")
            row = db.one("SELECT channel_id FROM slack_channels WHERE channel_id = ? OR name = ?", [ref, ref])
            if row is None:
                raise ToolError(f"channel_not_found: {value!r}.", category="not_found")
            channels_in.append(row["channel_id"])
        elif kind == "from":
            from_users.append(value.strip("<>@"))
        else:
            try:
                day = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                raise ToolError(f"{kind}: takes YYYY-MM-DD, not {value!r}.", category="invalid_input")
            if kind == "after":
                after = day.timestamp()
            else:
                before = day.timestamp() + 86400
    for bound, name in ((args.get("after"), "after"), (args.get("before"), "before")):
        if bound is not None:
            try:
                value = float(bound)
            except (TypeError, ValueError):
                raise ToolError(f"{name} must be a Unix timestamp string.", category="invalid_input")
            if name == "after":
                after = value
            else:
                before = value

    actor = db.one("SELECT user_id FROM current_actor WHERE id = 'default'")
    actor_id = actor["user_id"] if actor else "U01"
    caller, relayable = _reader(db)
    visible = {}
    for channel in db.query("SELECT * FROM slack_channels"):
        if relayable is not None and channel["channel_id"] not in relayable:
            continue
        member = db.one("SELECT 1 FROM slack_channel_members WHERE channel_id = ? AND user_id = ?", [channel["channel_id"], actor_id]) is not None
        if channel["is_private"] and not member:
            continue
        if args.get("only_my_channels") and not member:
            continue
        if ("private_channel" if channel["is_private"] else "public_channel") not in types:
            continue
        visible[channel["channel_id"]] = channel["name"]
    if channels_in:
        visible = {cid: name for cid, name in visible.items() if cid in channels_in}

    terms = [k.strip('"').lower() for k in keywords if k.strip('"')]
    found = []
    for m in db.query("SELECT message_id, channel_id, user_id, text, ts, thread_ts, permalink FROM slack_messages ORDER BY ts DESC"):
        if m["channel_id"] not in visible:
            continue
        if from_users and m["user_id"] not in from_users:
            continue
        at = float(m["ts"])
        if after is not None and at < after:
            continue
        if before is not None and at > before:
            continue
        # Matched on what the caller may see, so a search cannot confirm a withheld token.
        shown = _redact(db, caller, m["text"])
        text = shown.lower()
        if terms and not all(term in text for term in terms):
            continue
        found.append({
            "message_id": m["message_id"],
            "channel_id": m["channel_id"],
            "channel_name": visible[m["channel_id"]],
            "user_id": m["user_id"],
            "text": shown,
            "ts": m["ts"],
            "thread_ts": m["thread_ts"],
            "permalink": m["permalink"],
        })
    if sort_dir == "asc":
        found.reverse()

    page = found[offset : offset + limit]
    result = {"messages": page, "has_more": offset + limit < len(found)}
    if result["has_more"]:
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
