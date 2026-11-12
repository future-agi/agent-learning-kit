def ready(world):
    emp = next((e for e in world.state().get("employees", []) if e["employee_id"] == "E403" and e["status"] == "active"), None)
    if not emp: return "E403 missing"
    return None
