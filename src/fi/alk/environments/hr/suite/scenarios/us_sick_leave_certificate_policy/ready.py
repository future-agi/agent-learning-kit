def ready(world):
    state = world.state()
    pol = next((p for p in state.get("hr_policies", []) if p["topic"] == "sick_leave" and p["region"] == "US"), None)
    if not pol:
        return "US sick_leave policy missing in hr_policies"
    return None
