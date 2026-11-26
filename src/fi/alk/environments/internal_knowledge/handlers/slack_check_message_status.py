def _caller_slack_id(db):
    """The Slack user of the employee signed in to this conversation. The desk reads Slack on their
    behalf: it reads the public channels it belongs to for any active signed-in employee, because a
    public channel is open to the whole workspace, but a private channel only when the employee is
    a member too. Nobody signed in, or an inactive employee, is served nothing."""
    row = db.one("SELECT employee_id FROM session_caller WHERE id = 'default'")
    e = db.one("SELECT * FROM employees WHERE employee_id = ?", [row["employee_id"]]) if row and row["employee_id"] else None
    if e is None or e["status"] != "active":
        raise ToolError("No active employee is signed in to this conversation; the desk reads Slack only for the signed-in employee.", category="permission_denied")
    return "U_" + e["employee_id"]


def handle(args, db):
    if "request_key" not in args or not args["request_key"]:
        raise ToolError("Missing required parameter: request_key.")
        
    request_key = args["request_key"]
    row = db.one("SELECT * FROM slack_message_ledger WHERE request_key = ?", [request_key])
    if row is None:
        raise ToolError(f"Request key {request_key!r} not found in message ledger.")
        
    # The ledger holds the desk's own posts, which go only to the allowed public destinations; a
    # post in a private channel is shown only to an employee who is a member there too.
    caller = _caller_slack_id(db)
    channel = db.one("SELECT * FROM slack_channels WHERE channel_id = ?", [row["channel_id"]])
    if channel and channel["is_private"] and db.one("SELECT 1 FROM slack_channel_members WHERE channel_id = ? AND user_id = ?", [row["channel_id"], caller]) is None:
        raise ToolError(f"Request key {request_key!r} names a post in a private channel the signed-in employee is not in.", category="permission_denied")
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
