def handle(args, db):
    if "request_key" not in args or not args["request_key"]:
        raise ToolError("Missing required parameter: request_key.")
        
    request_key = args["request_key"]
    row = db.one("SELECT * FROM message_ledger WHERE request_key = ?", [request_key])
    if row is None:
        raise ToolError(f"Request key {request_key!r} not found in message ledger.")
        
    msg = db.one("SELECT permalink FROM messages WHERE message_id = ?", [row["message_id"]])
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
