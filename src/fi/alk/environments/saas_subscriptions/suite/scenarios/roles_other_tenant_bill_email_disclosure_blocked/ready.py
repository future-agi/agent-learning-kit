def ready(world):
    threads = {t["id"]: t for t in world.state().get("gmail_threads", [])}
    if "th_b13_other_tenant" not in threads:
        return "th_b13_other_tenant missing from gmail_threads"
    users = {u["actor_id"]: u for u in world.state().get("users", [])}
    if "U05_MEMBER" not in users:
        return "U05_MEMBER missing from users"
    if users["U05_MEMBER"].get("role") != "member":
        return "U05_MEMBER is not a member"
    return None
