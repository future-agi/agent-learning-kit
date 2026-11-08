def ready(world):
    state = world.state()
    if not any(c["customer_id"] == "C380" for c in state.get("customers", [])):
        return "C380 missing"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD380"), None)
    if not card or card["status"] != "active":
        return "CARD380 is missing or not active"
    return None
