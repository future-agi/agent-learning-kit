def ready(world):
    state = world.state()
    m02 = next((e for e in state.get("employees", []) if e["employee_id"] == "M02"), None)
    if not m02:
        return "M02 missing in employees"
    return None
