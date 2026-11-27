def ready(world):
    emp = next((e for e in world.state().get("employees", []) if e["employee_id"] == "E404" and e["status"] == "active"), None)
    if not emp: return "E404 missing"
    return None
