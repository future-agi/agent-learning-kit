def ready(world):
    state = world.state()
    bal = next((b for b in state.get("leave_balances", []) if b["employee_id"] == "E12" and b["leave_type"] == "annual"), None)
    if not bal:
        return "E12 annual leave balance missing"
    return None
