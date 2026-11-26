def ready(world):
    state = world.state()
    case = next((c for c in state.get("hr_cases", []) if c["case_id"] == "CASE-2902"), None)
    if not case:
        return "CASE-2902 missing"
    if case["status"] != "resolved":
        return f"CASE-2902 status is {case['status']}, expected resolved"
    return None
