def ready(world):
    state = world.state()
    if not any(c["customer_id"] == "C324" for c in state.get("customers", [])):
        return "C324 missing"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD324"), None)
    if not card or card["status"] != "active":
        return "CARD324 is missing or not active"
    tx = next((t for t in state.get("transactions", []) if t["transaction_id"] == "TX324"), None)
    if not tx or tx["status"] != "posted":
        return "TX324 is missing or not posted"
    return None
