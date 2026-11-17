def ready(world):
    state = world.state()
    cards = state.get("cards", [])
    txs = state.get("transactions", [])
    c = next((x for x in cards if x["card_id"] == "CARD101"), None)
    if not c or c["status"] != "active":
        return "CARD101 is missing or not active"
    t = next((x for x in txs if x["transaction_id"] == "TX101"), None)
    if not t or t["status"] != "posted":
        return "TX101 is missing or not posted"
    return None
