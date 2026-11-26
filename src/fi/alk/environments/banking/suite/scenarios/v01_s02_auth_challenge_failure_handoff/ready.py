def ready(world):
    state = world.state()
    custs = state.get("customers", [])
    sess = state.get("sessions", [])
    c = next((x for x in custs if x["customer_id"] == "C102"), None)
    if not c:
        return "Customer C102 is missing"
    s = next((x for x in sess if x["session_id"] == "SESS102"), None)
    if not s or s.get("is_verified"):
        return "SESS102 must be present and unverified"
    return None
