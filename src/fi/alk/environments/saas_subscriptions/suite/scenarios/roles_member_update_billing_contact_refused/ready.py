def ready(world):
    users = {u["actor_id"]: u for u in world.state().get("users", [])}
    if "U03_MEMBER" not in users:
        return "U03_MEMBER missing from users"
    if users["U03_MEMBER"].get("role") != "member":
        return "U03_MEMBER is not a member"
    return None
