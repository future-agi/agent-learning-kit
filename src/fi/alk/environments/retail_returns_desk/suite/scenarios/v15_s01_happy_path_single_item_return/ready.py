def ready(world):
    st = world.state()
    items = [i for i in st.get("order_items", []) if i["order_id"] == "O01" and i["sku"] == "SH01"]
    if not items or items[0]["quantity"] != 2:
        return "Order O01 must have 2 units of SH01"
    return None
