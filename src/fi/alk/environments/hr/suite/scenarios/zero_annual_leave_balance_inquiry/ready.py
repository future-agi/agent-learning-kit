def ready(world):
    bal = next((b for b in world.state().get("leave_balances", []) if b["employee_id"] == "E07" and b["leave_type"] == "annual"), None)
    if not bal or bal["available_days"] != 0:
        return "E07 zero annual leave balance missing"
    return None
