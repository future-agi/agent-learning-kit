def ready(world):
    state = world.state()
    if not any(c["customer_id"] == "C310" for c in state.get("customers", [])):
        return "C310 missing"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD310"), None)
    if not card or card["status"] != "active":
        return "CARD310 is missing or not active"
    return None
