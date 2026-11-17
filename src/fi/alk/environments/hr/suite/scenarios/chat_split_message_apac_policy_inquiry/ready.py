def ready(world):
    state = world.state()
    pol = next((p for p in state.get("hr_policies", []) if p["region"] == "APAC" and p["topic"] == "annual_leave"), None)
    if not pol:
        return "APAC annual leave policy missing"
    if pol["carry_forward_days"] != 7:
        return f"APAC carry forward is {pol['carry_forward_days']}, expected 7"
    return None
