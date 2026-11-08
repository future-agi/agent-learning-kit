def ready(world):
    state = world.state()
    if not any(c["customer_id"] == "C302" for c in state.get("customers", [])):
        return "C302 missing"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD302"), None)
    if not card or card["status"] != "active":
        return "CARD302 is missing or not active"
    return None
