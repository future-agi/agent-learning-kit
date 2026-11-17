def ready(world):
    state = world.state()
    if not any(c["customer_id"] == "C311" for c in state.get("customers", [])):
        return "C311 missing"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD311"), None)
    if not card or card["status"] != "locked":
        return "CARD311 is missing or not locked"
    return None
