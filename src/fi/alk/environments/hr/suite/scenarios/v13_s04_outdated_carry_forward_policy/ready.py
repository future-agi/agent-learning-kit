def ready(world):
    state = world.state()
    pol = next((p for p in state.get("hr_policies", []) if p["region"] == "US" and p["version"] == "v2.0" and p["topic"] == "annual_leave"), None)
    if not pol:
        return "US v2.0 annual leave policy missing"
    if int(pol["carry_forward_days"]) != 5:
        return f"US v2.0 carry forward is {pol['carry_forward_days']}, expected 5"
    return None
