def ready(world):
    state = world.state()
    case = next((c for c in state.get("hr_cases", []) if c["case_id"] == "CASE-2901"), None)
    if not case:
        return "CASE-2901 missing"
    if case["status"] != "in_review":
        return f"CASE-2901 status is {case['status']}, expected in_review"
    return None
