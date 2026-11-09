def ready(world):
    state = world.state()
    if not any(c["customer_id"] == "C366" for c in state.get("customers", [])):
        return "C366 missing"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD366"), None)
    if not card or card["status"] != "active":
        return "CARD366 is missing or not active"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD367"), None)
    if not card or card["status"] != "active":
        return "CARD367 is missing or not active"
    return None
