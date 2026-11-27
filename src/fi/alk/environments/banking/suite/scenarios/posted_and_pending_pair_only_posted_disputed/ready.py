def ready(world):
    state = world.state()
    if not any(c["customer_id"] == "C330" for c in state.get("customers", [])):
        return "C330 missing"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD330"), None)
    if not card or card["status"] != "active":
        return "CARD330 is missing or not active"
    tx = next((t for t in state.get("transactions", []) if t["transaction_id"] == "TX330"), None)
    if not tx or tx["status"] != "posted":
        return "TX330 is missing or not posted"
    tx = next((t for t in state.get("transactions", []) if t["transaction_id"] == "TX331"), None)
    if not tx or tx["status"] != "pending":
        return "TX331 is missing or not pending"
    return None
