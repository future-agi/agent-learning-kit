def ready(world):
    state = world.state()
    cards = state.get("cards", [])
    txs = state.get("transactions", [])
    c = next((x for x in cards if x["card_id"] == "CARD105"), None)
    if not c or c["status"] != "active":
        return "CARD105 must exist and be active"
    t = next((x for x in txs if x["transaction_id"] == "TX105"), None)
    if not t or t["status"] != "posted":
        return "TX105 must exist and be posted"
    return None
