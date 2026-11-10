def ready(world):
    users = {u["actor_id"]: u for u in world.state().get("users", [])}
    if "U11_MEMBER" not in users:
        return "U11_MEMBER missing from users"
    if users["U11_MEMBER"].get("role") != "member":
        return "U11_MEMBER is not a member"
    return None
