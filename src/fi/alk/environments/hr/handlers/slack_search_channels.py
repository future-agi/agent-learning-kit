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
    keywords = args.get("keywords") or []
    if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
        raise ToolError("keywords must be a list of words or quoted phrases", category="invalid_input")
    query = (args.get("natural_language_query") or "").strip()
    limit = args.get("limit") if args.get("limit") is not None else 20
    if not isinstance(limit, int) or limit < 1 or limit > 20:
        raise ToolError("limit must be an integer from 1 to 20", category="invalid_input")
    offset = 0
    if args.get("cursor"):
        try:
            offset = int(args["cursor"])
        except ValueError:
            raise ToolError(f"Invalid cursor: {args['cursor']!r}", category="invalid_input")
    types = [t.strip() for t in (args.get("channel_types") or "public_channel").split(",") if t.strip()]
    for t in types:
        if t not in ("public_channel", "private_channel"):
            raise ToolError(f"channel_types: {t!r} is not public_channel or private_channel", category="invalid_input")

    actor = db.one("SELECT user_id FROM current_actor WHERE id = 'default'")
    actor_id = actor["user_id"] if actor else "U01"
    terms = [k.strip('"').lower() for k in keywords if k.strip('"')]

    caller_user = _caller_slack_user(db)
    if caller_user is None:
        raise ToolError("Permission denied: nobody is signed in to this conversation.", category="permission_denied")
    found = []
    for channel in db.query("SELECT * FROM slack_channels ORDER BY name ASC"):
        # A private channel the caller is not in is not shown to exist, even when the desk is in it.
        if not _caller_may_read(db, channel, caller_user):
            continue
        member = db.one("SELECT 1 FROM slack_channel_members WHERE channel_id = ? AND user_id = ?", (channel["channel_id"], actor_id)) is not None
        private = bool(channel["is_private"])
        # A private channel is visible only to its members; the rest of the workspace cannot
        # learn it exists.
        if private and not member:
            continue
        if ("private_channel" if private else "public_channel") not in types:
            continue
        text = f"{channel['name']} {channel['topic']}".lower()
        if terms and not all(term in text for term in terms):
            continue
        if not terms and query and not any(word in text for word in query.lower().split()):
            continue
        count = db.one("SELECT COUNT(*) AS cnt FROM slack_channel_members WHERE channel_id = ?", (channel["channel_id"],))
        found.append({
            "channel_id": channel["channel_id"],
            "name": channel["name"],
            "topic": channel["topic"],
            "purpose": channel["topic"],
            "is_private": private,
            "is_member": member,
            "member_count": count["cnt"] if count else 0,
            "is_archived": False,
        })

    page = found[offset : offset + limit]
    result = {"channels": page}
    if offset + limit < len(found):
        result["next_cursor"] = str(offset + limit)
    return result
