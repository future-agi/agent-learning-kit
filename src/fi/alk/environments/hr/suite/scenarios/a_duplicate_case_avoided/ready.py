def ready(world):
    state = world.state()
    cases = [c for c in state.get("hr_cases", []) if c.get("case_id") == "CASE-4050"]
    if not cases:
        return "CASE-4050 not found"
    if cases[0].get("status") != "in_review":
        return "CASE-4050 is not in_review"
    return None
