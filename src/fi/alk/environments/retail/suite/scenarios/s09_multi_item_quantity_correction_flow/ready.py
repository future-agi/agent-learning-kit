def ready(world):
    st = world.state()
    items = [i for i in st.get("order_items", []) if i["order_id"] == "O09" and i["sku"] == "AP12"]
    if not items or items[0]["quantity"] != 1:
        return "Order O09 item AP12 must have quantity 1"
    return None
