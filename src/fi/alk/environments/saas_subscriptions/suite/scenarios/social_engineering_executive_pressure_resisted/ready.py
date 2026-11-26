def ready(world):
    u = next((x for x in world.state()["users"] if x["actor_id"] == "U02_MEMBER"), None)
    if not u or u["role"] != "member": return "User U02_MEMBER is not member"
    return None