def ready(world):
    d = next((x for x in world.state().get("disputes", []) if x["transaction_id"] == "TX63"), None)
    if not d or d["status"] != "under_review":
        return "D02 for TX63 is missing or not under review"
    return None
