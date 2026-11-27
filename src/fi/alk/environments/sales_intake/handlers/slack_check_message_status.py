def handle(args, db):
    if "request_key" not in args or not args["request_key"]:
        raise ToolError("Missing required parameter: request_key.")
    # The ledger holds the text the desk posted in internal channels.
    if _caller(db)[0] != "staff":
        raise ToolError("Permission denied: BrightDesk's Slack is internal; only BrightDesk staff in this conversation can read its post ledger.", category="permission_denied")
        
    request_key = args["request_key"]
    row = db.one("SELECT * FROM slack_message_ledger WHERE request_key = ?", [request_key])
    if row is None:
        raise ToolError(f"Request key {request_key!r} not found in message ledger.")
        
    msg = db.one("SELECT permalink FROM slack_messages WHERE message_id = ?", [row["message_id"]])
    permalink = msg["permalink"] if msg else f"https://slack.example.com/archives/{row['channel_id']}/{row['message_id']}"
    
    return {
        "channel_id": row["channel_id"],
        "created_at": row["created_at"],
        "message_id": row["message_id"],
        "permalink": permalink,
        "request_key": row["request_key"],
        "status": row["status"],
        "text": row["text"],
        "thread_ts": row["thread_ts"]
    }


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
