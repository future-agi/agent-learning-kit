def handle(args, db):
    if "request_key" not in args or not args["request_key"]:
        raise ToolError("Missing required parameter: request_key.")
        
    request_key = args["request_key"]
    row = db.one("SELECT * FROM slack_message_ledger WHERE request_key = ?", [request_key])
    if row is None:
        raise ToolError(f"Request key {request_key!r} not found in message ledger.")
        
    caller = _may_relay(db, row["channel_id"])
    msg = db.one("SELECT permalink FROM slack_messages WHERE message_id = ?", [row["message_id"]])
    permalink = msg["permalink"] if msg else f"https://slack.example.com/archives/{row['channel_id']}/{row['message_id']}"
    
    return {
        "channel_id": row["channel_id"],
        "created_at": row["created_at"],
        "message_id": row["message_id"],
        "permalink": permalink,
        "request_key": row["request_key"],
        "status": row["status"],
        "text": _redact(db, caller, row["text"]),
        "thread_ts": row["thread_ts"]
    }


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
