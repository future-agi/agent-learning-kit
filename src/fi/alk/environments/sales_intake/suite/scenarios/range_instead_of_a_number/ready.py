def ready(world):
    state = world.state()
    contact = next((c for c in state.get("contacts", []) if c["email"] == "avril.bouchard@quillon.example"), None)
    if not contact:
        return "C425 missing"
    return None
