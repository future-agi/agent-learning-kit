def ready(world):
    card = next((c for c in world.state().get("cards", []) if c["card_id"] == "CARD23"), None)
    if not card or card["status"] != "active":
        return "CARD23 is missing or not active"
    return None
