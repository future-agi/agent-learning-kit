def ready(world):
    emp = next((e for e in world.state().get("employees", []) if e["employee_id"] == "E14" and e["name"] == "Nina Simone"), None)
    if not emp: return "E14 missing"
    return None
