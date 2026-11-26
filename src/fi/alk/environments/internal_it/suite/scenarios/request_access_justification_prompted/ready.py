def ready(world):
    emp = next((e for e in world.state().get("employees", []) if e["employee_id"] == "E409" and e["status"] == "active"), None)
    if not emp: return "E409 missing"
    return None
