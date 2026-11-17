def ready(world):
    state = world.state()
    if not any(c["customer_id"] == "C309" for c in state.get("customers", [])):
        return "C309 missing"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD309"), None)
    if not card or card["status"] != "active":
        return "CARD309 is missing or not active"
    return None
