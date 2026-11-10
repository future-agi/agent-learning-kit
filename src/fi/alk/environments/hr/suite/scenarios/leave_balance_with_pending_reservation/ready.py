def ready(world):
    bal = next((b for b in world.state().get("leave_balances", []) if b["employee_id"] == "E02" and b["leave_type"] == "annual"), None)
    if not bal or bal["reserved_days"] != 3:
        return "E02 annual leave balance with 3 reserved days missing"
    return None
