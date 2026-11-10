def ready(world):
    emps = world.state()["employees"]
    emp = next((e for e in emps if e["employee_id"] == "M416"), None)
    if not emp:
        return "M416 not found in employees"
    if emp["role"] != "engineering_manager":
        return f"M416 role is {emp['role']}, expected engineering_manager"
    return None
