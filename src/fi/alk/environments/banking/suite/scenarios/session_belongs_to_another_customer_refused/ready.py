def ready(world):
    state = world.state()
    if not any(c["customer_id"] == "C372" for c in state.get("customers", [])):
        return "C372 missing"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD372"), None)
    if not card or card["status"] != "active":
        return "CARD372 is missing or not active"
    return None
