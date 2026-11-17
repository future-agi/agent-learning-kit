def ready(world):
    state = world.state()
    cards = state.get("cards", [])
    c = next((x for x in cards if x["card_id"] == "CARD112"), None)
    if not c or c.get("status") != "active":
        return "CARD112 must exist and be active"
    return None
