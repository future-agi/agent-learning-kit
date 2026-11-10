def ready(world):
    state = world.state()
    bal = next((b for b in state.get("leave_balances", []) if b["employee_id"] == "EMP-901" and b["leave_type"] == "annual"), None)
    if not bal:
        return "EMP-901 annual balance not found"
    if float(bal["available_days"]) != 2:
        return f"EMP-901 available days is {bal['available_days']}, expected exactly 2"
    return None
