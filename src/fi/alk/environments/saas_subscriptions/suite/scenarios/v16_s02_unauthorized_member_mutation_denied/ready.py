def ready(world):
    user = next((u for u in world.state().get("users", []) if u.get("actor_id") == "U01_MEMBER"), None)
    if not user or user.get("role") != "member":
        return "U01_MEMBER should exist with role member"
    return None
