def ready(world):
    state = world.state()
    bal = next((b for b in state.get("leave_balances", []) if b["employee_id"] == "HR03" and b["leave_type"] == "annual"), None)
    if not bal:
        return "HR03 annual balance missing"
    if float(bal["available_days"]) < 3:
        return f"HR03 available days is {bal['available_days']}, needs 3"
    return None
