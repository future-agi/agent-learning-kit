def ready(world):
    state = world.state()
    bal = next((b for b in state.get("leave_balances", []) if b["employee_id"] == "E05" and b["leave_type"] == "annual"), None)
    if not bal:
        return "E05 annual balance not found"
    if float(bal["available_days"]) < 2:
        return f"E05 available days is {bal['available_days']}, needs at least 2"
    return None
