def ready(world):
    state = world.state()
    if not any(c["customer_id"] == "C322" for c in state.get("customers", [])):
        return "C322 missing"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD322"), None)
    if not card or card["status"] != "active":
        return "CARD322 is missing or not active"
    tx = next((t for t in state.get("transactions", []) if t["transaction_id"] == "TX322"), None)
    if not tx or tx["status"] != "posted":
        return "TX322 is missing or not posted"
    return None
