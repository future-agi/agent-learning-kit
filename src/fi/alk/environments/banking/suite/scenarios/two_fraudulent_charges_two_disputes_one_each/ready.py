def ready(world):
    state = world.state()
    if not any(c["customer_id"] == "C327" for c in state.get("customers", [])):
        return "C327 missing"
    card = next((c for c in state.get("cards", []) if c["card_id"] == "CARD327"), None)
    if not card or card["status"] != "active":
        return "CARD327 is missing or not active"
    tx = next((t for t in state.get("transactions", []) if t["transaction_id"] == "TX327"), None)
    if not tx or tx["status"] != "posted":
        return "TX327 is missing or not posted"
    tx = next((t for t in state.get("transactions", []) if t["transaction_id"] == "TX328"), None)
    if not tx or tx["status"] != "posted":
        return "TX328 is missing or not posted"
    tx = next((t for t in state.get("transactions", []) if t["transaction_id"] == "TX329"), None)
    if not tx or tx["status"] != "posted":
        return "TX329 is missing or not posted"
    return None
