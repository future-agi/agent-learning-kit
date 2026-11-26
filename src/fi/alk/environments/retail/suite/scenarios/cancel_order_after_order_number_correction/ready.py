def ready(world):
    orders = {o["order_id"]: o for o in world.state().get("orders", [])}
    if "O431" not in orders or "O435" not in orders:
        return "Orders O431 and O435 must be present"
    if orders["O431"]["status"] != "processing" or orders["O435"]["status"] != "processing":
        return "Both orders must be in processing status"
    return None
