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

    _caller, relayable = _reader(db)
    found = []
    for channel in db.query("SELECT * FROM slack_channels ORDER BY name ASC"):
        if relayable is not None and channel["channel_id"] not in relayable:
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
