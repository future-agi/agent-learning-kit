def ready(world):
    state = world.state()
    emp = next((e for e in state.get("employees", []) if e["employee_id"] == "E05"), None)
    if not emp:
        return "E05 missing"
    case = next((c for c in state.get("hr_cases", []) if c["case_id"] == "CASE-2901"), None)
    if not case:
        return "CASE-2901 missing"
    return None
