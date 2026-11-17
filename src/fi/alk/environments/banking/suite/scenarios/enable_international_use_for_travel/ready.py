def ready(world):
    state = world.state()
    if not any(c["customer_id"] == "C308" for c in state.get("customers", [])):
        return "C308 missing"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD308"), None)
    if not card or card["status"] != "active":
        return "CARD308 is missing or not active"
    return None
