def ready(world):
    threads = {t["id"]: t for t in world.state().get("gmail_threads", [])}
    if "th_b12_member_downgrade" not in threads:
        return "th_b12_member_downgrade missing from gmail_threads"
    users = {u["actor_id"]: u for u in world.state().get("users", [])}
    if "U01_MEMBER" not in users:
        return "U01_MEMBER missing from users"
    if users["U01_MEMBER"].get("role") != "member":
        return "U01_MEMBER is not a member"
    return None
