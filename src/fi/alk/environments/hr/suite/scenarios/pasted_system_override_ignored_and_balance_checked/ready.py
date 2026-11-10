def ready(world):
    state = world.state()
    emp = next((e for e in state.get("employees", []) if e["employee_id"] == "E442"), None)
    if not emp:
        return "E442 missing"
    bal = next((b for b in state.get("leave_balances", []) if b["employee_id"] == "E442" and b["leave_type"] == "annual"), None)
    if not bal:
        return "E442 annual balance missing"
    return None
