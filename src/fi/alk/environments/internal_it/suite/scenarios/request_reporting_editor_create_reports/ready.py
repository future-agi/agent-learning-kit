def ready(world):
    emp = next((e for e in world.state().get("employees", []) if e["employee_id"] == "E402" and e["status"] == "active"), None)
    if not emp: return "E402 missing or inactive"
    return None
