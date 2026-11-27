def ready(world):
    users = {u["actor_id"]: u for u in world.state().get("users", [])}
    if "U02_MEMBER" not in users:
        return "missing U02_MEMBER"
    if users["U02_MEMBER"].get("role") != "member":
        return "U02_MEMBER is not a member"
    return None
