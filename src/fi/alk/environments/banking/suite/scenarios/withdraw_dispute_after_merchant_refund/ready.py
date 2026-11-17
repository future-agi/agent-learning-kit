def ready(world):
    state = world.state()
    if not any(c["customer_id"] == "C340" for c in state.get("customers", [])):
        return "C340 missing"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD340"), None)
    if not card or card["status"] != "active":
        return "CARD340 is missing or not active"
    tx = next((t for t in state.get("transactions", []) if t["transaction_id"] == "TX340"), None)
    if not tx or tx["status"] != "posted":
        return "TX340 is missing or not posted"
    d = next((x for x in state.get("disputes", []) if x["dispute_id"] == "D340"), None)
    if not d or d["status"] != "under_review":
        return "D340 is missing or not under_review"
    return None
