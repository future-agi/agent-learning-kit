def ready(world):
    st = world.state()
    items = [i for i in st.get("order_items", []) if i["order_id"] == "O401" and i["sku"] == "AP05"]
    if not items or items[0]["quantity"] != 2:
        return "Order O401 item AP05 must have quantity 2"
    return None
