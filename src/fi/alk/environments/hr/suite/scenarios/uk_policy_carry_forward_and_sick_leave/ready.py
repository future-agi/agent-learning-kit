def ready(world):
    state = world.state()
    pol = next((p for p in state.get("hr_policies", []) if p["region"] == "UK" and p["topic"] == "annual_leave" and p["version"] == "v2.0"), None)
    if not pol:
        return "UK v2.0 annual leave policy missing"
    if pol["carry_forward_days"] != 8:
        return f"UK carry forward days is {pol['carry_forward_days']}, expected 8"
    return None
