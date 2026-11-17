def ready(world):
    state = world.state()
    bal = next((b for b in state.get("leave_balances", []) if b["employee_id"] == "E10" and b["leave_type"] == "annual"), None)
    if not bal:
        return "E10 annual balance not found"
    if float(bal["available_days"]) < 2:
        return f"E10 available days is {bal['available_days']}, needs 2"
    return None
