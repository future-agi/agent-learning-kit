def ready(world):
    state = world.state()
    if not any(c["customer_id"] == "C345" for c in state.get("customers", [])):
        return "C345 missing"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD345"), None)
    if not card or card["status"] != "active":
        return "CARD345 is missing or not active"
    return None
