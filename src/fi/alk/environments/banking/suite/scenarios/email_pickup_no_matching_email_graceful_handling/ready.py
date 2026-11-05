def ready(world):
    state = world.state()
    txs = state.get("transactions", [])
    if not any(t["transaction_id"] == "TX07" for t in txs):
        return "TX07 missing"
    return None