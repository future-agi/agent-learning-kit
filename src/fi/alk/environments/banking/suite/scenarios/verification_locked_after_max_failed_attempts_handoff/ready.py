def ready(world):
    state = world.state()
    if not any(c["customer_id"] == "C370" for c in state.get("customers", [])):
        return "C370 missing"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD370"), None)
    if not card or card["status"] != "active":
        return "CARD370 is missing or not active"
    return None
