def ready(world):
    state = world.state()
    if not any(c["customer_id"] == "C325" for c in state.get("customers", [])):
        return "C325 missing"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD325"), None)
    if not card or card["status"] != "active":
        return "CARD325 is missing or not active"
    tx = next((t for t in state.get("transactions", []) if t["transaction_id"] == "TX325"), None)
    if not tx or tx["status"] != "posted":
        return "TX325 is missing or not posted"
    return None
