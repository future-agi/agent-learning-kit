def ready(world):
    state = world.state()
    disputes = state.get("disputes", [])
    d = next((x for x in disputes if x["transaction_id"] == "TX104"), None)
    if not d or d["request_key"] != "REQ-C104-TX104":
        return "Dispute D104 with request key REQ-C104-TX104 must exist"
    return None
