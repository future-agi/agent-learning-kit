def ready(world):
    state = world.state()
    if not any(c["customer_id"] == "C360" for c in state.get("customers", [])):
        return "C360 missing"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD360"), None)
    if not card or card["status"] != "active":
        return "CARD360 is missing or not active"
    return None
