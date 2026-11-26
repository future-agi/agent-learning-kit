def ready(world):
    emp = next((e for e in world.state().get("employees", []) if e["employee_id"] == "E402"), None)
    if not emp:
        return "E402 missing in employees"
    return None
