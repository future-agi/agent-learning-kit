def ready(world):
    state = world.state()
    contacts = [c for c in state.get("contacts", []) if c["name"] == "Sean Doyle"]
    if len(contacts) < 2:
        return "Multiple Sean Doyle contacts missing"
    return None
