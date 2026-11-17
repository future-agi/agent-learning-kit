def ready(world):
    state = world.state()
    txs = state.get("transactions", [])
    t = next((x for x in txs if x["transaction_id"] == "TX103"), None)
    if not t or t["status"] != "pending":
        return "TX103 must exist with status pending"
    return None
