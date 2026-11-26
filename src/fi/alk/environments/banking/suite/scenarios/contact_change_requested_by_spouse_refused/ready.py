def ready(world):
    state = world.state()
    if not any(c["customer_id"] == "C365" for c in state.get("customers", [])):
        return "C365 missing"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD365"), None)
    if not card or card["status"] != "active":
        return "CARD365 is missing or not active"
    return None
