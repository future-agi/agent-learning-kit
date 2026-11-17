def ready(world):
    state = world.state()
    pol = next((p for p in state.get("hr_policies", []) if p["topic"] == "parental_leave"), None)
    if not pol:
        return "parental_leave policy missing in hr_policies"
    return None
