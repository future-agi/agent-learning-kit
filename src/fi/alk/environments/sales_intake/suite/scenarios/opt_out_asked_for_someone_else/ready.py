def ready(world):
    state = world.state()
    contact = next((c for c in state.get("contacts", []) if c["email"] == "tane.henare@kiwifreight.example"), None)
    if not contact:
        return "C422 missing"
    return None
