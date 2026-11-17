def ready(world):
    emps = {e["employee_id"]: e for e in world.state().get("employees", [])}
    if "E411" not in emps:
        return "employee E411 is missing"
    return None
