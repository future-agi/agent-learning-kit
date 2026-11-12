def ready(world):
    t = next((t for t in world.state().get("tenants", []) if t.get("tenant_id") == "T401"), None)
    if not t:
        return "T401 tenant must exist"
    return None
