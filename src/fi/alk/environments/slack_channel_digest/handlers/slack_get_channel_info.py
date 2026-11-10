def handle(args, db):
    actor_id = args.get("actor_id")
    channel_id = args.get("channel_id")
    if not actor_id or not channel_id:
        raise ToolError("Missing required parameters: actor_id and channel_id.")
    
    user = db.one("SELECT * FROM users WHERE user_id = ?", [actor_id])
    if user is None:
        raise ToolError(f"User {actor_id!r} not found.")
        
    channel = db.one("SELECT * FROM channels WHERE channel_id = ?", [channel_id])
    if channel is None:
        raise ToolError(f"Channel {channel_id!r} not found.")
        
    membership = db.one("SELECT * FROM channel_members WHERE channel_id = ? AND user_id = ?", [channel_id, actor_id])
    is_member = membership is not None
    
    if channel["is_private"] and not is_member:
        raise ToolError(f"Access denied: Actor {actor_id!r} is not a member of private channel {channel_id!r}.")
        
    count_row = db.one("SELECT COUNT(*) as cnt FROM channel_members WHERE channel_id = ?", [channel_id])
    member_count = count_row["cnt"] if count_row else 0
    
    return {
        "channel_id": channel["channel_id"],
        "name": channel["name"],
        "topic": channel["topic"],
        "is_private": bool(channel["is_private"]),
        "is_member": is_member,
        "member_count": member_count
    }
