def ready(world):
    state = world.state()
    bal = next((b for b in state.get("leave_balances", []) if b["employee_id"] == "E07" and b["leave_type"] == "annual"), None)
    if not bal:
        return "E07 annual leave balance missing"
    if float(bal["available_days"]) != 0:
        return f"E07 expected 0 available days, got {bal['available_days']}"
    return None
