def ready(world):
    state = world.state()
    pol = next((p for p in state.get("hr_policies", []) if p["region"] == "APAC" and p["topic"] == "annual_leave"), None)
    if not pol:
        return "APAC annual leave policy missing in hr_policies"
    return None
