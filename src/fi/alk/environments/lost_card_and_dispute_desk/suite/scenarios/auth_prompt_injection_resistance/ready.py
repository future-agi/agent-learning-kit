def ready(world):
    state = world.state()
    cards = state.get("cards", [])
    c = next((x for x in cards if x["card_id"] == "CARD107"), None)
    if not c:
        return "CARD107 must exist"
    return None
