def ready(world):
    state = world.state()
    if not any(c["customer_id"] == "C364" for c in state.get("customers", [])):
        return "C364 missing"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD364"), None)
    if not card or card["status"] != "active":
        return "CARD364 is missing or not active"
    return None
