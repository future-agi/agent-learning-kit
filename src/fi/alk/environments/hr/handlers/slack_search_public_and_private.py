def _caller_slack_user(db):
    """The Slack user of the person signed in to this conversation, matched to their employee record
    by email; None when nobody is signed in."""
    row = db.one("SELECT employee_id FROM session_caller WHERE id = 'default'")
    if not row or not row["employee_id"]:
        return None
    user = db.one(
        "SELECT s.user_id FROM employees e JOIN slack_users s ON lower(s.email) = lower(e.email) WHERE e.employee_id = ?",
        (row["employee_id"],),
    )
    return user["user_id"] if user else ""


def _caller_may_read(db, channel, caller_user):
    """Whether the desk may show this channel's messages to the person signed in. The desk being in
    a channel is necessary, never enough: a public channel is open to every employee, so the desk
    reads it for whoever is signed in; a private one (#eng-managers) only for a caller who is a
    member of it, whatever the desk itself can see."""
    if caller_user is None:
        return False
    if not channel["is_private"]:
        return True
    return db.one(
        "SELECT 1 FROM slack_channel_members WHERE channel_id = ? AND user_id = ?",
        (channel["channel_id"], caller_user),
    ) is not None


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
    caller_user = _caller_slack_user(db)
    if caller_user is None:
        raise ToolError("Permission denied: nobody is signed in to this conversation.", category="permission_denied")
    visible = {}
    for channel in db.query("SELECT * FROM slack_channels"):
        # Results and their text are what a read would show: only channels the caller may read.
        if not _caller_may_read(db, channel, caller_user):
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
        text = m["text"].lower()
        if terms and not all(term in text for term in terms):
            continue
        found.append({
            "message_id": m["message_id"],
            "channel_id": m["channel_id"],
            "channel_name": visible[m["channel_id"]],
            "user_id": m["user_id"],
            "text": m["text"],
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
