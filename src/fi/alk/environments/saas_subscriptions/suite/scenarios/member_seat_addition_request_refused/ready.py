def ready(world):
    user = next((u for u in world.state()["users"] if u["actor_id"] == "U10_MEMBER" and u["tenant_id"] == "T10"), None)
    if not user:
        return "User U10_MEMBER in T10 not found"
    if user["role"] != "member":
        return f"User U10_MEMBER has role {user['role']}, expected member"
    return None
