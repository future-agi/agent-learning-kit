def ready(world):
    reqs = {r["request_id"]: r for r in world.state().get("leave_requests", [])}
    if "REQ-4102" not in reqs or reqs["REQ-4102"]["status"] != "approved":
        return "REQ-4102 is missing or not approved"
    return None
