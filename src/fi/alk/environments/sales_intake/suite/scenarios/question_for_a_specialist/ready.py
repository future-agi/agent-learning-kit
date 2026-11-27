def ready(world):
    state = world.state()
    contact = next((c for c in state.get("contacts", []) if c["email"] == "lukas.brandstetter@alpenwerk.example"), None)
    if not contact:
        return "C424 missing"
    return None
