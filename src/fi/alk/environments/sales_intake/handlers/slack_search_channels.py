def handle(args, db):
    keywords = args.get("keywords") or []
    # The workspace directory is internal. A staff caller sees what the desk itself (the acting Slack user)
    # may see: public channels and the private ones the desk is a member of.
    if _caller(db)[0] != "staff":
        raise ToolError("Permission denied: BrightDesk's Slack is internal; only BrightDesk staff in this conversation can search its channels.", category="permission_denied")
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

    found = []
    for channel in db.query("SELECT * FROM slack_channels ORDER BY name ASC"):
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
