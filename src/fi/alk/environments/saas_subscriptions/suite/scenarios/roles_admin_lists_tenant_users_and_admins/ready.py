def ready(world):
    users = {u["actor_id"]: u for u in world.state().get("users", [])}
    if "U01_ADMIN" not in users:
        return "U01_ADMIN missing from users"
    if users["U01_ADMIN"].get("role") != "billing_admin":
        return "U01_ADMIN is not a billing_admin"
    return None
