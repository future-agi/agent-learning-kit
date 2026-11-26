def ready(world):
    state = world.state()
    if not any(c["customer_id"] == "C361" for c in state.get("customers", [])):
        return "C361 missing"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD361"), None)
    if not card or card["status"] != "active":
        return "CARD361 is missing or not active"
    return None
