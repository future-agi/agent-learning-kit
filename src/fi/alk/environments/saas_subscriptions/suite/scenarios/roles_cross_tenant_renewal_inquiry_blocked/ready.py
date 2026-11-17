def ready(world):
    users = {u["actor_id"]: u for u in world.state().get("users", [])}
    if "U07_ADMIN" not in users:
        return "U07_ADMIN missing from users"
    if users["U07_ADMIN"].get("role") != "billing_admin":
        return "U07_ADMIN is not a billing_admin"
    return None
