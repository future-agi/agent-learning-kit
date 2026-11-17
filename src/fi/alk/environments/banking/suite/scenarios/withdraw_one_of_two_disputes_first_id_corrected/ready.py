def ready(world):
    state = world.state()
    if not any(c["customer_id"] == "C341" for c in state.get("customers", [])):
        return "C341 missing"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD341"), None)
    if not card or card["status"] != "active":
        return "CARD341 is missing or not active"
    tx = next((t for t in state.get("transactions", []) if t["transaction_id"] == "TX341"), None)
    if not tx or tx["status"] != "posted":
        return "TX341 is missing or not posted"
    tx = next((t for t in state.get("transactions", []) if t["transaction_id"] == "TX342"), None)
    if not tx or tx["status"] != "posted":
        return "TX342 is missing or not posted"
    d = next((x for x in state.get("disputes", []) if x["dispute_id"] == "D341"), None)
    if not d or d["status"] != "open":
        return "D341 is missing or not open"
    d = next((x for x in state.get("disputes", []) if x["dispute_id"] == "D342"), None)
    if not d or d["status"] != "under_review":
        return "D342 is missing or not under_review"
    return None
