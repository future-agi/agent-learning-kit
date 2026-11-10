def ready(world):
    state = world.state()
    pol = next((p for p in state.get("hr_policies", []) if p["topic"] == "bereavement_leave"), None)
    if not pol:
        return "bereavement_leave policy missing in hr_policies"
    return None
