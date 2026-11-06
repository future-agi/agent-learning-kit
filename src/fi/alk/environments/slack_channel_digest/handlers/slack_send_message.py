import json

def handle(args, db):
    channel_id = args.get("channel_id")
    text = args.get("message")
    if not channel_id or not isinstance(channel_id, str):
        raise ToolError("Missing required parameter: channel_id.", category="invalid_input")
    if not text or not isinstance(text, str):
        raise ToolError("Missing required parameter: message.", category="invalid_input")
    if len(text) > 5000:
        raise ToolError("message is limited to 5000 characters.", category="invalid_input")
    thread_ts = args.get("thread_ts")
    approval_id = args.get("approval_id")
    request_key = args.get("request_key")

    actor = db.one("SELECT user_id FROM current_actor WHERE id = 'default'")
    actor_id = actor["user_id"] if actor else "U01"
    channel = db.one("SELECT * FROM channels WHERE channel_id = ?", [channel_id])
    if channel is None:
        raise ToolError(f"channel_not_found: {channel_id!r}.", category="not_found")
    member = db.one("SELECT 1 FROM channel_members WHERE channel_id = ? AND user_id = ?", [channel_id, actor_id])
    if channel["is_private"] and member is None:
        raise ToolError(f"Access denied: you are not a member of private channel {channel_id!r}.", category="permission_denied")
    if thread_ts and db.one("SELECT 1 FROM messages WHERE channel_id = ? AND ts = ?", [channel_id, thread_ts]) is None:
        raise ToolError(f"thread_not_found: no message {thread_ts!r} in channel {channel_id!r}.", category="not_found")

    if "@channel" in text or "@here" in text or "@everyone" in text:
        raise ToolError("Broadcast mentions (@channel, @here, @everyone) are forbidden.", category="policy_blocked")

    dest_pol = db.one("SELECT value FROM environment_policy WHERE name = 'allowed_destinations'", [])
    if dest_pol:
        allowed_list = [c.strip() for c in json.loads(dest_pol["value"]).split(",") if c.strip()]
        if channel_id not in allowed_list:
            raise ToolError(f"Destination channel {channel_id!r} is not in allowed destinations: {allowed_list}.", category="policy_blocked")
    mode_pol = db.one("SELECT value FROM environment_policy WHERE name = 'mode'", [])
    mode = json.loads(mode_pol["value"]) if mode_pol else "approval_gated"
    if mode == "disabled":
        raise ToolError("Posting is disabled by policy.", category="policy_blocked")
    len_pol = db.one("SELECT value FROM environment_policy WHERE name = 'max_length'", [])
    if len_pol and len(text) > int(json.loads(len_pol["value"])):
        raise ToolError(f"Message length {len(text)} exceeds max length {json.loads(len_pol['value'])}.", category="policy_blocked")

    # Approval-gated posting (workbook C06): the approval names the actor, the destination and
    # the content it covers.
    if mode == "approval_gated":
        if not approval_id:
            raise ToolError("Posting needs an approval_id: ask for approval of the exact text and destination first.", category="policy_blocked")
        app = db.one("SELECT * FROM approvals WHERE approval_id = ?", [approval_id])
        if app is None:
            raise ToolError(f"Approval {approval_id!r} not found.", category="not_found")
        if app["status"] != "approved":
            raise ToolError(f"Approval {approval_id!r} is not approved (status: {app['status']}).", category="policy_blocked")
        if app["actor_id"] != actor_id:
            raise ToolError(f"Approval {approval_id!r} belongs to another user.", category="policy_blocked")
        if app["channel_id"] != channel_id:
            raise ToolError(f"Approval {approval_id!r} is for channel {app['channel_id']!r}, not {channel_id!r}.", category="policy_blocked")

    # Idempotency (workbook C07): a repeated request_key returns the message already posted.
    if request_key:
        existing = db.one("SELECT * FROM message_ledger WHERE request_key = ?", [request_key])
        if existing:
            msg = db.one("SELECT * FROM messages WHERE message_id = ?", [existing["message_id"]])
            return {
                "channel_id": existing["channel_id"],
                "ts": msg["ts"] if msg else "",
                "message_id": existing["message_id"],
                "permalink": msg["permalink"] if msg else f"https://slack.example.com/archives/{existing['channel_id']}/{existing['message_id']}",
                "status": "idempotent_replay",
                "request_key": request_key,
                "thread_ts": existing["thread_ts"],
            }

    cnt = db.one("SELECT COUNT(*) as c FROM messages", [])["c"]
    message_id = f"M{cnt + 1:02d}"
    ts = f"1729900000.{cnt + 1000:06d}"
    permalink = f"https://slack.example.com/archives/{channel_id}/p{ts.replace('.', '')}"
    clock_row = db.one("SELECT now FROM clock", [])
    now_str = clock_row["now"] if clock_row else "2026-10-26T12:00:00Z"
    db.execute(
        "INSERT INTO messages (message_id, channel_id, user_id, text, ts, thread_ts, permalink) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [message_id, channel_id, actor_id, text, ts, thread_ts, permalink],
    )
    db.execute(
        "INSERT INTO message_ledger (request_key, message_id, channel_id, thread_ts, text, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [request_key or message_id, message_id, channel_id, thread_ts, text, "delivered", now_str],
    )
    return {
        "channel_id": channel_id,
        "ts": ts,
        "message_id": message_id,
        "permalink": permalink,
        "status": "delivered",
        "request_key": request_key or message_id,
        "thread_ts": thread_ts,
    }
