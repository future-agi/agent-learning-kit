def ready(world):
    state = world.state()
    if not any(c["customer_id"] == "C363" for c in state.get("customers", [])):
        return "C363 missing"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD363"), None)
    if not card or card["status"] != "active":
        return "CARD363 is missing or not active"
    return None
