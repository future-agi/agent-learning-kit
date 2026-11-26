def ready(world):
    state = world.state()
    contact = next((c for c in state.get("contacts", []) if c["email"] == "brooke.winslow@kestrellogistics.example"), None)
    if not contact:
        return "C423 missing"
    return None
