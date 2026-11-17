def ready(world):
    state = world.state()
    if not any(c["customer_id"] == "C362" for c in state.get("customers", [])):
        return "C362 missing"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD362"), None)
    if not card or card["status"] != "active":
        return "CARD362 is missing or not active"
    return None
