def ready(world):
    bal = next((b for b in world.state().get("leave_balances", []) if b["employee_id"] == "E404" and b["leave_type"] == "sick"), None)
    if not bal or bal["available_days"] != 7:
        return "E404 sick leave balance missing or wrong"
    return None
