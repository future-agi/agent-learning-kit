def handle(args, db):
    if "request_key" not in args or not args["request_key"]:
        raise ToolError("Missing required parameter: request_key.")
        
    request_key = args["request_key"]
    row = db.one("SELECT * FROM slack_message_ledger WHERE request_key = ?", [request_key])
    if row is None:
        raise ToolError(f"Request key {request_key!r} not found in message ledger.")
        
    msg = db.one("SELECT permalink, ts FROM slack_messages WHERE message_id = ?", [row["message_id"]])
    # The ledger repeats the posted text, so it answers only someone who may see that post.
    if not _may_see_message(db, {"channel_id": row["channel_id"], "thread_ts": row["thread_ts"], "ts": msg["ts"] if msg else ""}):
        raise ToolError(f"Request key {request_key!r} names a post you may not see.", category="permission_denied")
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


def _may_see_message(db, message):
    """Whether the person signed in to the conversation (session_caller) may see this Slack message.
    The desk reads Slack as itself (current_actor): only public channels and the private channels it
    is a member of, which the caller above has already applied. For a Cloudnest staff caller that is
    the whole answer: #billing-escalations is the shared staff channel the desk is in, and the desk
    reads it on their behalf. A tenant's user is a customer, not staff: they see only the threads
    filed under their own tenant (slack_thread_tenants), never another tenant's escalation. Nobody
    signed in sees nothing."""
    row = db.one("SELECT actor_id FROM session_caller WHERE id = 'default'")
    caller = row["actor_id"] if row else None
    if not caller:
        return False
    if db.one("SELECT 1 FROM slack_users WHERE user_id = ?", [caller]) is not None:
        return True
    user = db.one("SELECT tenant_id FROM users WHERE actor_id = ?", [caller])
    if user is None:
        return False
    thread = message["thread_ts"] or message["ts"]
    filed = db.one("SELECT tenant_id FROM slack_thread_tenants WHERE channel_id = ? AND thread_ts = ?", [message["channel_id"], thread])
    return filed is not None and filed["tenant_id"] == user["tenant_id"]
