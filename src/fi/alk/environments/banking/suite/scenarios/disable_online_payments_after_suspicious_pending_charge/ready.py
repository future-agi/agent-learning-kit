def ready(world):
    state = world.state()
    if not any(c["customer_id"] == "C307" for c in state.get("customers", [])):
        return "C307 missing"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD307"), None)
    if not card or card["status"] != "active":
        return "CARD307 is missing or not active"
    tx = next((t for t in state.get("transactions", []) if t["transaction_id"] == "TX307"), None)
    if not tx or tx["status"] != "pending":
        return "TX307 is missing or not pending"
    return None
