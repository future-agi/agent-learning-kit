def ready(world):
    emps = world.state()["employees"]
    emp = next((e for e in emps if e["employee_id"] == "E416"), None)
    if not emp:
        return "E416 not found in employees"
    if emp["role"] != "data_analyst":
        return f"E416 role is {emp['role']}, expected data_analyst"
    return None
