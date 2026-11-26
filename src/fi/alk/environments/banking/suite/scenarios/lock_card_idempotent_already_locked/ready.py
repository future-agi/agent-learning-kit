def ready(world):
    card = next((c for c in world.state().get("cards", []) if c["card_id"] == "CARD116"), None)
    if not card or card.get("status") != "locked":
        return "CARD116 must already be locked (the app lock of last night)"
    return None
