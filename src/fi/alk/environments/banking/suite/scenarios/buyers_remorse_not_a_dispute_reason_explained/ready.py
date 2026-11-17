def ready(world):
    state = world.state()
    if not any(c["customer_id"] == "C333" for c in state.get("customers", [])):
        return "C333 missing"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD333"), None)
    if not card or card["status"] != "active":
        return "CARD333 is missing or not active"
    tx = next((t for t in state.get("transactions", []) if t["transaction_id"] == "TX333"), None)
    if not tx or tx["status"] != "posted":
        return "TX333 is missing or not posted"
    return None
