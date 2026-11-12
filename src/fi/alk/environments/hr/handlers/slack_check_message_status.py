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
    if "request_key" not in args or not args["request_key"]:
        raise ToolError("Missing required parameter: request_key.")
        
    request_key = args["request_key"]
    row = db.one("SELECT * FROM slack_message_ledger WHERE request_key = ?", [request_key])
    if row is None:
        raise ToolError(f"Request key {request_key!r} not found in message ledger.")
        
    channel = db.one("SELECT * FROM slack_channels WHERE channel_id = ?", [row["channel_id"]])
    if channel is None or not _caller_may_read(db, channel, _caller_slack_user(db)):
        raise ToolError(f"Access denied: the person signed in may not read channel {row['channel_id']!r}.", category="permission_denied")
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
