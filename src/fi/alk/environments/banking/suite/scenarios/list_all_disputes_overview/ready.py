def ready(world):
    ids = {x["dispute_id"] for x in world.state().get("disputes", []) if x["customer_id"] == "C11"}
    if not {"D04", "D05"} <= ids:
        return "Kevin's disputes D04 and D05 are missing"
    return None
