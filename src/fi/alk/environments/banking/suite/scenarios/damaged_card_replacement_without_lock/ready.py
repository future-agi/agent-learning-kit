def ready(world):
    state = world.state()
    if not any(c["customer_id"] == "C303" for c in state.get("customers", [])):
        return "C303 missing"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD303"), None)
    if not card or card["status"] != "active":
        return "CARD303 is missing or not active"
    return None
