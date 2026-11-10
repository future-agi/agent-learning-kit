def ready(world):
    emps = [e for e in world.state()["employees"] if e.get("employee_id") == "E423"]
    if not emps:
        return "E423 not found"
    return None