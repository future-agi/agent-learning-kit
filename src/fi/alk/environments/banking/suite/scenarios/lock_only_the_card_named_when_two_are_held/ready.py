def ready(world):
    state = world.state()
    if not any(c["customer_id"] == "C312" for c in state.get("customers", [])):
        return "C312 missing"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD312"), None)
    if not card or card["status"] != "active":
        return "CARD312 is missing or not active"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD313"), None)
    if not card or card["status"] != "active":
        return "CARD313 is missing or not active"
    return None
