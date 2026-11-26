def ready(world):
    state = world.state()
    if not any(c["customer_id"] == "C306" for c in state.get("customers", [])):
        return "C306 missing"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD306"), None)
    if not card or card["status"] != "locked":
        return "CARD306 is missing or not locked"
    return None
