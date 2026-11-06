def ready(world):
    state = world.state()
    txs = state.get("transactions", [])
    if not any(t["transaction_id"] == "TX04" for t in txs):
        return "TX04 missing"
    return None