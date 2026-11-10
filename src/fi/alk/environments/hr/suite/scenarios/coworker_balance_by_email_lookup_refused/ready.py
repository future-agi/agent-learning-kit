def ready(world):
    state = world.state()
    emp1 = next((e for e in state.get("employees", []) if e["employee_id"] == "E441"), None)
    if not emp1:
        return "E441 missing"
    emp2 = next((e for e in state.get("employees", []) if e["email"] == "bob.smith@example.com"), None)
    if not emp2:
        return "bob.smith@example.com missing"
    return None
