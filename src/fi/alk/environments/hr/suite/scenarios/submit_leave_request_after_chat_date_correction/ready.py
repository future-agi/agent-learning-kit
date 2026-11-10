def ready(world):
    emps = {e["employee_id"]: e for e in world.state().get("employees", [])}
    if "E412" not in emps:
        return "employee E412 is missing"
    bals = [b for b in world.state().get("leave_balances", []) if b["employee_id"] == "E412" and b["leave_type"] == "annual"]
    if not bals or bals[0]["available_days"] != 10:
        return "E412 annual balance is not 10 available days"
    return None
