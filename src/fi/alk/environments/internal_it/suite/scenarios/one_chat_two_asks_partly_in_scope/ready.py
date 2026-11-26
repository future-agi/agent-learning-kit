def ready(world):
    state = world.state()
    emp = next((e for e in state.get("employees", []) if e["employee_id"] == "E441"), None)
    if not emp or emp["status"] != "active":
        return "missing active employee E441"
    return None
