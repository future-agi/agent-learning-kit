def ready(world):
    state = world.state()
    if not any(c["customer_id"] == "C304" for c in state.get("customers", [])):
        return "C304 missing"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD304"), None)
    if not card or card["status"] != "active":
        return "CARD304 is missing or not active"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD305"), None)
    if not card or card["status"] != "active":
        return "CARD305 is missing or not active"
    return None
