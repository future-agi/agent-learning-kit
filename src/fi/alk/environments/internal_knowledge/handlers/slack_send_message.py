import json

def _plain(text):
    """Text as approved: the same words, whatever the spacing."""
    return " ".join((text or "").split())


def _handle(args, db):
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
    channel = db.one("SELECT * FROM slack_channels WHERE channel_id = ?", [channel_id])
    if channel is None:
        raise ToolError(f"channel_not_found: {channel_id!r}.", category="not_found")
    member = db.one("SELECT 1 FROM slack_channel_members WHERE channel_id = ? AND user_id = ?", [channel_id, actor_id])
    if channel["is_private"] and member is None:
        raise ToolError(f"Access denied: you are not a member of private channel {channel_id!r}.", category="permission_denied")
    if thread_ts and db.one("SELECT 1 FROM slack_messages WHERE channel_id = ? AND ts = ?", [channel_id, thread_ts]) is None:
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
    clock_row = db.one("SELECT now FROM clock", [])
    now_str = clock_row["now"] if clock_row else "2026-10-26T12:00:00Z"
    if mode == "approval_gated":
        if not approval_id:
            raise ToolError("Posting needs an approval_id: ask for approval of the exact text and destination first.", category="policy_blocked")
        app = db.one("SELECT * FROM slack_approvals WHERE approval_id = ?", [approval_id])
        if app is None:
            raise ToolError(f"Approval {approval_id!r} not found.", category="not_found")
        if app["status"] == "used":
            raise ToolError(f"Approval {approval_id!r} was already used for a post; one approval covers one post.", category="policy_blocked")
        if app["status"] != "approved":
            raise ToolError(f"Approval {approval_id!r} is not approved (status: {app['status']}).", category="policy_blocked")
        if app["actor_id"] != actor_id:
            raise ToolError(f"Approval {approval_id!r} belongs to another user.", category="policy_blocked")
        if app["channel_id"] != channel_id:
            raise ToolError(f"Approval {approval_id!r} is for channel {app['channel_id']!r}, not {channel_id!r}.", category="policy_blocked")
        if _plain(app["approved_text"]) != _plain(text):
            raise ToolError(f"Approval {approval_id!r} covers different text; post exactly the approved text, or ask for approval of this one.", category="policy_blocked")
        if app["expires_at"] and app["expires_at"] <= now_str:
            raise ToolError(f"Approval {approval_id!r} expired at {app['expires_at']}.", category="policy_blocked")

    # Idempotency (workbook C07): a repeated request_key returns the message already posted, and
    # only that message: the same key for another destination, thread or text is a different request.
    if request_key:
        existing = db.one("SELECT * FROM slack_message_ledger WHERE request_key = ?", [request_key])
        if existing:
            if (existing["channel_id"], existing["thread_ts"] or None, _plain(existing["text"])) != (channel_id, thread_ts or None, _plain(text)):
                raise ToolError(f"request_key {request_key!r} was already used for a different message; use a new key for a new message.", category="conflict")
            msg = db.one("SELECT * FROM slack_messages WHERE message_id = ?", [existing["message_id"]])
            return {
                "channel_id": existing["channel_id"],
                "ts": msg["ts"] if msg else "",
                "message_id": existing["message_id"],
                "permalink": msg["permalink"] if msg else f"https://slack.example.com/archives/{existing['channel_id']}/{existing['message_id']}",
                "status": "idempotent_replay",
                "request_key": request_key,
                "thread_ts": existing["thread_ts"],
            }

    cnt = db.one("SELECT COUNT(*) as c FROM slack_messages", [])["c"]
    message_id = f"M{cnt + 1:02d}"
    # Stamped at the world's clock, so a reply sorts after the message it answers.
    from datetime import datetime
    ts = f"{int(datetime.fromisoformat(now_str.replace('Z', '+00:00')).timestamp())}.{cnt + 1000:06d}"
    permalink = f"https://slack.example.com/archives/{channel_id}/p{ts.replace('.', '')}"
    db.execute(
        "INSERT INTO slack_messages (message_id, channel_id, user_id, text, ts, thread_ts, permalink) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [message_id, channel_id, actor_id, text, ts, thread_ts, permalink],
    )
    db.execute(
        "INSERT INTO slack_message_ledger (request_key, message_id, channel_id, thread_ts, text, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [request_key or message_id, message_id, channel_id, thread_ts, text, "delivered", now_str],
    )
    if mode == "approval_gated":
        db.execute("UPDATE slack_approvals SET status = 'used' WHERE approval_id = ?", [approval_id])
    return {
        "channel_id": channel_id,
        "ts": ts,
        "message_id": message_id,
        "permalink": permalink,
        "status": "delivered",
        "request_key": request_key or message_id,
        "thread_ts": thread_ts,
    }


_WRITES = ("slack_approvals", "slack_message_ledger", "slack_messages")

def _all_or_nothing(db, run):
    """Run the call so that a failed call changes nothing. A handler gets no transaction of its own,
    so every table this tool writes is held first and put back if the call raises, whether a write
    failed partway or the call refused after writing. Only a fault the world simulates on purpose
    (an error marked keep_writes: a rival booking, a commit whose reply was lost, a fault used up)
    keeps what it wrote."""
    held = {table: db.query(f"SELECT * FROM {table}") for table in _WRITES}
    try:
        return run()
    except Exception as failed:
        if not getattr(failed, "keep_writes", False):
            for table, rows in held.items():
                if _same(db.query(f"SELECT * FROM {table}"), rows):
                    continue
                db.execute(f"DELETE FROM {table}")
                for row in rows:
                    cols = list(row)
                    db.execute(
                        f"INSERT INTO {table} ({', '.join(chr(34) + c + chr(34) for c in cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [row[c] for c in cols],
                    )
        raise

def _same(rows, held):
    """Whether a table still holds exactly the rows it held, in any order: only a table the call
    changed is rewritten."""
    import json

    def key(row):
        return json.dumps(row, sort_keys=True, default=str)

    return sorted(map(key, rows)) == sorted(map(key, held))


def handle(args, db):
    return _all_or_nothing(db, lambda: _handle(args, db))
