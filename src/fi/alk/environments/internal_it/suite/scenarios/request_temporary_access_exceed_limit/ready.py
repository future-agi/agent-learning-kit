def ready(world):
    emps = [e for e in world.state()["employees"] if e.get("employee_id") == "E420"]
    if not emps:
        return "E420 not found"
    return None