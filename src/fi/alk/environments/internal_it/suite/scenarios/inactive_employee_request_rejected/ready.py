def ready(world):
    emp = next((e for e in world.state().get("employees", []) if e["employee_id"] == "E20" and e["status"] == "inactive"), None)
    if not emp: return "E20 missing or not inactive"
    return None
