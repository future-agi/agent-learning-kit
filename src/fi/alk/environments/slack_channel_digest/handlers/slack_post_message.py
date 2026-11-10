import json

def handle(args, db):
    if "actor_id" not in args or not args["actor_id"]:
        raise ToolError("Missing required parameter: actor_id.")
    if "approval_id" not in args or not args["approval_id"]:
        raise ToolError("Missing required parameter: approval_id.")
    if "channel_id" not in args or not args["channel_id"]:
        raise ToolError("Missing required parameter: channel_id.")
    if "request_key" not in args or not args["request_key"]:
        raise ToolError("Missing required parameter: request_key.")
    if "text" not in args or not args["text"]:
        raise ToolError("Missing required parameter: text.")
        
    actor_id = args["actor_id"]
    approval_id = args["approval_id"]
    channel_id = args["channel_id"]
    request_key = args["request_key"]
    text = args["text"]
    thread_ts = args.get("thread_ts")
    
    user = db.one("SELECT * FROM users WHERE user_id = ?", [actor_id])
    if user is None:
        raise ToolError(f"User {actor_id!r} not found.")
        
    channel = db.one("SELECT * FROM channels WHERE channel_id = ?", [channel_id])
    if channel is None:
        raise ToolError(f"Channel {channel_id!r} not found.")
        
    membership = db.one("SELECT * FROM channel_members WHERE channel_id = ? AND user_id = ?", [channel_id, actor_id])
    if channel["is_private"] and membership is None:
        raise ToolError(f"Access denied: Actor {actor_id!r} is not a member of private channel {channel_id!r}.")
        
    # Check mentions
    if "@channel" in text or "@here" in text or "@everyone" in text:
        raise ToolError("Broadcast mentions (@channel, @here, @everyone) are forbidden.")
        
    # Check configurable environment policies
    dest_pol = db.one("SELECT value FROM environment_policy WHERE name = 'allowed_destinations'", [])
    if dest_pol:
        allowed_csv = json.loads(dest_pol["value"])
        allowed_list = [c.strip() for c in allowed_csv.split(",") if c.strip()]
        if channel_id not in allowed_list:
            raise ToolError(f"Destination channel {channel_id!r} is not in allowed destinations: {allowed_list}.")
            
    mode_pol = db.one("SELECT value FROM environment_policy WHERE name = 'mode'", [])
    if mode_pol:
        mode = json.loads(mode_pol["value"])
        if mode == "disabled":
            raise ToolError("Posting is disabled by policy.")
            
    len_pol = db.one("SELECT value FROM environment_policy WHERE name = 'max_length'", [])
    if len_pol:
        max_len = int(json.loads(len_pol["value"]))
        if len(text) > max_len:
            raise ToolError(f"Message length {len(text)} exceeds max length {max_len}.")
            
    # Check approval
    app = db.one("SELECT * FROM approvals WHERE approval_id = ?", [approval_id])
    if app is None:
        raise ToolError(f"Approval {approval_id!r} not found.")
    if app["status"] != "approved":
        raise ToolError(f"Approval {approval_id!r} is not approved (status: {app['status']}).")
    if app["actor_id"] != actor_id:
        raise ToolError(f"Approval {approval_id!r} belongs to actor {app['actor_id']!r}, not {actor_id!r}.")
    if app["channel_id"] != channel_id:
        raise ToolError(f"Approval {approval_id!r} is for channel {app['channel_id']!r}, not {channel_id!r}.")
        
    # Check idempotency
    existing = db.one("SELECT * FROM message_ledger WHERE request_key = ?", [request_key])
    if existing:
        msg = db.one("SELECT * FROM messages WHERE message_id = ?", [existing["message_id"]])
        return {
            "channel_id": existing["channel_id"],
            "message_id": existing["message_id"],
            "permalink": msg["permalink"] if msg else f"https://slack.example.com/archives/{existing['channel_id']}/{existing['message_id']}",
            "request_key": request_key,
            "status": "idempotent_replay",
            "thread_ts": existing["thread_ts"]
        }
        
    # Insert new message
    cnt_row = db.one("SELECT COUNT(*) as c FROM messages", [])
    cnt = cnt_row["c"] if cnt_row else 0
    message_id = f"M{cnt + 1:02d}"
    ts = f"1729900000.{cnt + 1000:06d}"
    permalink = f"https://slack.example.com/archives/{channel_id}/p{ts.replace('.', '')}"
    
    clock_row = db.one("SELECT now FROM clock", [])
    now_str = clock_row["now"] if clock_row else "2026-10-26T12:00:00Z"
    
    db.execute(
        "INSERT INTO messages (message_id, channel_id, user_id, text, ts, thread_ts, permalink) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [message_id, channel_id, actor_id, text, ts, thread_ts, permalink]
    )
    db.execute(
        "INSERT INTO message_ledger (request_key, message_id, channel_id, thread_ts, text, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [request_key, message_id, channel_id, thread_ts, text, "delivered", now_str]
    )
    
    return {
        "channel_id": channel_id,
        "message_id": message_id,
        "permalink": permalink,
        "request_key": request_key,
        "status": "delivered",
        "thread_ts": thread_ts
    }
