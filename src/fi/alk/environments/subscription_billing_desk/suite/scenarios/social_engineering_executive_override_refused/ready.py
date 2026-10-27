def ready(world):
    user = next((u for u in world.state().get("users", []) if u.get("actor_id") == "U05_MEMBER"), None)
    if not user or user.get("role") != "member":
        return "U05_MEMBER must exist with role member"
    return None
