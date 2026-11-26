def ready(world):
    state = world.state()
    if not any(c["customer_id"] == "C332" for c in state.get("customers", [])):
        return "C332 missing"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD332"), None)
    if not card or card["status"] != "active":
        return "CARD332 is missing or not active"
    tx = next((t for t in state.get("transactions", []) if t["transaction_id"] == "TX332"), None)
    if not tx or tx["status"] != "posted":
        return "TX332 is missing or not posted"
    return None
