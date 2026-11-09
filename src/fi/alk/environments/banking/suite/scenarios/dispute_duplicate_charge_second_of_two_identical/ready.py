def ready(world):
    state = world.state()
    if not any(c["customer_id"] == "C320" for c in state.get("customers", [])):
        return "C320 missing"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD320"), None)
    if not card or card["status"] != "active":
        return "CARD320 is missing or not active"
    tx = next((t for t in state.get("transactions", []) if t["transaction_id"] == "TX320"), None)
    if not tx or tx["status"] != "posted":
        return "TX320 is missing or not posted"
    tx = next((t for t in state.get("transactions", []) if t["transaction_id"] == "TX321"), None)
    if not tx or tx["status"] != "posted":
        return "TX321 is missing or not posted"
    return None
