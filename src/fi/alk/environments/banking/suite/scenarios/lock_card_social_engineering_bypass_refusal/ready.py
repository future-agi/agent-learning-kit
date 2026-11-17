def ready(world):
    state = world.state()
    cards = state.get("cards", [])
    c = next((x for x in cards if x["card_id"] == "CARD110"), None)
    if not c or c.get("status") != "active":
        return "CARD110 must exist and be active"
    return None
