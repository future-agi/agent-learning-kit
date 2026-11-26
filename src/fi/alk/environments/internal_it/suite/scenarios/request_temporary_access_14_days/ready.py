def ready(world):
    emps = [e for e in world.state()["employees"] if e.get("employee_id") == "E419"]
    if not emps:
        return "E419 not found"
    return None