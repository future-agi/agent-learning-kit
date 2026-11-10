def ready(world):
    emp = next((e for e in world.state().get("employees", []) if e["employee_id"] == "E401"), None)
    if not emp:
        return "E401 missing in employees"
    return None
