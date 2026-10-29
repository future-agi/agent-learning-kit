def ready(world):
    state = world.state()
    txs = state.get("transactions", [])
    t = next((x for x in txs if x["transaction_id"] == "TX113"), None)
    if not t or t.get("status") != "posted":
        return "TX113 must exist and be posted"
    return None
