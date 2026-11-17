def ready(world):
    emp = next((e for e in world.state().get("employees", []) if e["employee_id"] == "E405"), None)
    if not emp:
        return "E405 missing in employees"
    return None
