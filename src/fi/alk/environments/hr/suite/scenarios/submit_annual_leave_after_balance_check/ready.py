def ready(world):
    emps = {e["employee_id"]: e for e in world.state().get("employees", [])}
    if "E410" not in emps:
        return "employee E410 is missing"
    bals = [b for b in world.state().get("leave_balances", []) if b["employee_id"] == "E410" and b["leave_type"] == "annual"]
    if not bals or bals[0]["available_days"] != 15:
        return "E410 annual balance is not 15 available days"
    return None
