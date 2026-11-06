def ready(world):
    state = world.state()
    bal = next((b for b in state.get("leave_balances", []) if b["employee_id"] == "E01" and b["leave_type"] == "annual"), None)
    if not bal:
        return "E01 annual leave balance not found"
    if float(bal["available_days"]) < 3:
        return f"E01 has only {bal['available_days']} available days, needs 3"
    return None
