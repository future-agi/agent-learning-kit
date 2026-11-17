def ready(world):
    state = world.state()
    bal = next((b for b in state.get("leave_balances", []) if b["employee_id"] == "HR02" and b["leave_type"] == "annual"), None)
    if not bal:
        return "HR02 annual leave balance missing"
    if float(bal["available_days"]) != 15:
        return f"HR02 available days is {bal['available_days']}, expected 15"
    return None
