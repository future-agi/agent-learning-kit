def ready(world):
    sc = next((c for c in world.state()["scheduled_changes"] if c["change_id"] == "PC05"), None)
    if not sc:
        return "Scheduled change PC05 not found in scheduled_changes"
    user = next((u for u in world.state()["users"] if u["actor_id"] == "U05_MEMBER"), None)
    if not user or user["role"] != "member":
        return "User U05_MEMBER is missing or not a member"
    return None